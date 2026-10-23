"""Q5 HMS: catalog decode, action payloads, API (read + clear/action publish)."""

from __future__ import annotations

import json
import time

import redis as redis_lib

from app.printers.bambu import hms_action_payload
from app.printers.base import command_channel, state_key
from app.services import hms

CREATE = {"name": "A1 mini", "host": "192.168.1.50", "serial": "0309ABC", "access_code": "12345678"}
KNOWN = "0300010000010001"  # present in hms_catalog.json


def test_decode_known_code_has_text_and_wiki():
    f = hms.decode_hms(0x03000100, 0x00010001)
    assert f["full_code"] == KNOWN and f["code"] == "0300_0100_0001_0001"
    assert f["module"] == "mainboard" and f["severity"] == "fatal"
    assert f["description"]
    assert f["wiki_url"].endswith("/hmscode/0300_0100_0001_0001")


def test_decode_unknown_code_never_raises():
    f = hms.decode_hms(0x7F7F7F7F, 0x7F7F0001)
    assert f["description"] is None and f["actions"] == []
    assert f["wiki_url"].endswith("7F7F_7F7F_7F7F_0001")
    assert hms.decode_hms("x", None)["code"]  # garbage in, still a dict


def test_decode_state_skips_status_codes_and_adds_print_error():
    state = {
        "hms": [{"attr": 0x03000100, "code": 0x00010001}, {"attr": 1, "code": 0x10}],
        "print_error": 0x0300400C,
    }
    out = hms.decode_state(state, "0309ABC")
    assert [f["kind"] for f in out] == ["hms", "print_error"]
    assert out[1]["severity"] == "fatal"
    assert hms.decode_state(None) == []


def test_actions_lookup_by_model():
    _, actions = hms._load()
    model, codes = next(iter(actions.items()))
    code, expected = next(iter(codes.items()))
    assert hms.actions_for(model, code) == expected
    assert hms.actions_for("ZZZ", code) == []


def test_action_payloads():
    assert hms_action_payload("0500400E", "STOP_PRINTING", None)[0]["print"]["command"] == "stop"
    ign = hms_action_payload("0500400E", "IGNORE_RESUME", "j1")[0]["print"]
    assert ign["err"] == str(0x0500400E) and ign["job_id"] == "j1"
    assert hms_action_payload("0500400E", "CHECK_ASSISTANT", None) == []
    assert hms_action_payload("0500400E", "NOPE", None) is None
    assert len(hms_action_payload("0500400E", "DBL_CHECK_OK", None)) == 2


async def test_hms_api_decodes_state(authenticated_client, printer_enabled, redis_url):
    pid = (await authenticated_client.post("/api/printers", json=CREATE)).json()["id"]
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), json.dumps({"hms": [{"attr": 0x03000100, "code": 0x00010001}]}))
    c.close()
    body = (await authenticated_client.get(f"/api/printers/{pid}/hms")).json()
    assert len(body) == 1 and body[0]["full_code"] == KNOWN


async def test_hms_clear_and_action_use_command_bus(
    authenticated_client, printer_enabled, redis_url
):
    import threading

    from app.printers.base import cmd_result_key

    pid = (await authenticated_client.post("/api/printers", json=CREATE)).json()["id"]
    c = redis_lib.Redis.from_url(redis_url)
    c.set(state_key(pid), json.dumps({"gcode_state": "IDLE", "dev_mode": True}))
    ps = c.pubsub()
    ps.subscribe(command_channel(pid))
    got: list[dict] = []
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            m = ps.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if m and m["type"] == "message":
                env = json.loads(m["data"])
                got.append(env)
                c.set(cmd_result_key(pid, env["request_id"]), json.dumps({"ok": True}), ex=30)

    threading.Thread(target=loop, daemon=True).start()
    time.sleep(0.2)
    try:
        r = await authenticated_client.post(f"/api/printers/{pid}/hms/clear")
        assert r.status_code == 200
        body = {"print_error": "0500400E", "action": "STOP_PRINTING"}
        r = await authenticated_client.post(f"/api/printers/{pid}/hms/action", json=body)
        assert r.status_code == 200
        bad = {"print_error": "0500400E", "action": "NOPE"}
        r = await authenticated_client.post(f"/api/printers/{pid}/hms/action", json=bad)
        assert r.status_code == 422
        r = await authenticated_client.post(
            f"/api/printers/{pid}/hms/action", json={"print_error": "zz", "action": "ABORT"}
        )
        assert r.status_code == 422
    finally:
        stop.set()
        c.close()
    assert [e["command"] for e in got] == ["clear_hms", "hms_action"]
    assert got[1]["args"]["action"] == "STOP_PRINTING" and got[1]["request_id"]


def test_decode_state_malformed_items_never_raise():
    out = hms.decode_state({"hms": [{"attr": 1, "code": "zz"}, {"code": [1]}, "x", None]})
    assert len(out) == 2 and all(f["description"] is None for f in out)
    assert hms.decode_state({"hms": "nope"}) == []
