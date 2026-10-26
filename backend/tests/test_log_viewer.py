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


def _seed(r, n=5):
    for i in range(1, n + 1):
        r.lpush(
            ls.LOG_LIST_KEY,
            json.dumps(
                {
                    "id": i,
                    "ts": 1700000000 + i,
                    "level": "ERROR" if i == 4 else "INFO",
                    "logger": "t",
                    "service": "printerd" if i % 2 else "api",
                    "msg": f"line {i} token=sekret",
                }
            ),
        )


async def test_logs_api(authenticated_client: httpx.AsyncClient, r):
    _seed(r)
    body = (await authenticated_client.get("/api/admin/logs")).json()
    assert [e["id"] for e in body["entries"]] == [1, 2, 3, 4, 5]
    assert body["last_id"] == 5 and "sekret" not in json.dumps(body)
    body = (await authenticated_client.get("/api/admin/logs?level=ERROR")).json()
    assert [e["id"] for e in body["entries"]] == [4]
    body = (await authenticated_client.get("/api/admin/logs?service=printerd&after=1")).json()
    assert [e["id"] for e in body["entries"]] == [3, 5]
    body = (await authenticated_client.get("/api/admin/logs?q=line 2&limit=1")).json()
    assert [e["id"] for e in body["entries"]] == [2]
    dl = await authenticated_client.get("/api/admin/logs/download?level=ERROR")
    assert dl.status_code == 200 and "line 4" in dl.text and "sekret" not in dl.text
    assert "attachment" in dl.headers["content-disposition"]


async def test_logs_admin_only(viewer_client: httpx.AsyncClient):
    assert (await viewer_client.get("/api/admin/logs")).status_code == 403
    assert (await viewer_client.get("/api/admin/logs/download")).status_code == 403
