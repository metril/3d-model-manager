"""Admin log viewer: redis log feed, redaction, filters, API."""

import json
import logging

import httpx
import pytest
import redis as redis_lib

from app.services import log_stream as ls


@pytest.fixture
def r(redis_url):
    client = redis_lib.Redis.from_url(redis_url)
    client.delete(ls.LOG_LIST_KEY, ls.LOG_SEQ_KEY)
    yield client
    client.delete(ls.LOG_LIST_KEY, ls.LOG_SEQ_KEY)
    client.close()


def test_redact_line():
    out = ls.redact_line(
        "connect mqtt://bob:hunter2@broker:1883 access_code=12345678 "
        'Authorization: Bearer abcdefghijkl1234 {"password": "p4ss"} token=abc'
    )
    for leak in ("hunter2", "12345678", "abcdefghijkl1234", "p4ss", "token=abc"):
        assert leak not in out
    assert "mqtt://***@broker:1883" in out


def test_handler_ships_records_with_service(r, redis_url):
    h = ls.RedisLogHandler(redis_url, "printerd")
    lg = logging.getLogger("tdmm.test.logship")
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    lg.addHandler(h)
    try:
        lg.info("hello password=secret1")
        lg.error("boom")
    finally:
        lg.removeHandler(h)
    entries = ls.parse_entries(r.lrange(ls.LOG_LIST_KEY, 0, -1))
    assert [e["msg"] for e in entries] == ["boom", "hello password=***"]  # newest first
    assert entries[0]["id"] > entries[1]["id"] and entries[0]["service"] == "printerd"


def test_handler_survives_dead_redis():
    h = ls.RedisLogHandler("redis://127.0.0.1:1/0", "api")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "m", (), None)
    h.emit(rec)  # must not raise
    h.emit(rec)  # backoff path


def test_filter_entries():
    es = [
        {"id": 3, "level": "ERROR", "service": "api", "logger": "a", "msg": "disk full"},
        {"id": 2, "level": "INFO", "service": "beat", "logger": "b", "msg": "tick"},
        {"id": 1, "level": "DEBUG", "service": "api", "logger": "c", "msg": "noise"},
    ]
    assert [e["id"] for e in ls.filter_entries(es, level="INFO")] == [3, 2]
    assert [e["id"] for e in ls.filter_entries(es, service="api", q="DISK")] == [3]
    assert [e["id"] for e in ls.filter_entries(es, after=2)] == [3]


BASE = 10**9  # ids far above anything the live handler emits during the test


def _seed(r, n=5):
    for i in range(1, n + 1):
        r.lpush(
            ls.LOG_LIST_KEY,
            json.dumps(
                {
                    "id": BASE + i,
                    "ts": 1700000000 + i,
                    "level": "ERROR" if i == 4 else "INFO",
                    "logger": "t",
                    "service": "svc-a" if i % 2 else "svc-b",
                    "msg": f"seeded line {i} token=sekret",
                }
            ),
        )


async def test_logs_api(authenticated_client: httpx.AsyncClient, r):
    _seed(r)
    base = "/api/admin/logs?q=seeded"
    body = (await authenticated_client.get(base)).json()
    assert [e["id"] - BASE for e in body["entries"]] == [1, 2, 3, 4, 5]
    assert body["last_id"] == BASE + 5 and "sekret" not in json.dumps(body)
    body = (await authenticated_client.get(f"{base}&level=ERROR")).json()
    assert [e["id"] - BASE for e in body["entries"]] == [4]
    body = (await authenticated_client.get(f"{base}&service=svc-a&after={BASE + 1}")).json()
    assert [e["id"] - BASE for e in body["entries"]] == [3, 5]
    body = (await authenticated_client.get("/api/admin/logs?q=seeded line 2&limit=1")).json()
    assert [e["id"] - BASE for e in body["entries"]] == [2]
    dl = await authenticated_client.get("/api/admin/logs/download?q=seeded&level=ERROR")
    assert dl.status_code == 200 and "seeded line 4" in dl.text and "sekret" not in dl.text
    assert "attachment" in dl.headers["content-disposition"]


async def test_logs_admin_only(viewer_client: httpx.AsyncClient):
    assert (await viewer_client.get("/api/admin/logs")).status_code == 403
    assert (await viewer_client.get("/api/admin/logs/download")).status_code == 403


@pytest.mark.parametrize(
    "line",
    [
        "refresh_token=abc123xyz",
        '{"access_token": "abc123xyz"}',
        "GET /x?client_secret=abc123xyz&a=1",
        "auth_token=abc123xyz",
        "bambu_access_code=abc123xyz",
        "x-api-key: abc123xyz",
    ],
)
def test_redact_prefixed_keys(line):
    assert "abc123xyz" not in ls.redact_line(line)


def test_install_queue_nonblocking_and_drops():
    import queue as q

    lg = logging.getLogger("tdmm.test.install")
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    h = ls.install("redis://127.0.0.1:1/0", "api", lg)
    qh = next(x for x in lg.handlers if isinstance(x, ls.DropQueueHandler))
    try:
        qh.listener.stop()
        qh.queue = q.Queue(maxsize=1)
        lg.info("a")
        lg.info("b")  # full: dropped, no block/raise
        assert qh.dropped == 1
        assert ls.install("redis://x", "api", lg) is h
    finally:
        lg.removeHandler(qh)


def test_reconfigure_does_not_leak_listener_threads(redis_url):
    import threading

    from app.logging_config import configure_logging

    configure_logging("INFO", "api")
    before = threading.active_count()
    for _ in range(5):
        configure_logging("INFO", "api")  # dictConfig closes the replaced handler
    assert threading.active_count() <= before + 1
