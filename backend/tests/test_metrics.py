"""Q7 /metrics: bearer-token auth (admin owner), Prometheus text body."""

from __future__ import annotations

import json

import pytest
import redis as redis_lib

from app.models import SmartPlug
from app.printers.base import state_key
from app.services import api_tokens
from app.tasks import base

pytestmark = pytest.mark.usefixtures("printer_enabled")

CREATE = {"name": "P", "host": "10.0.0.5", "serial": "0309ABC", "access_code": "12345678"}


async def test_requires_bearer(client, authenticated_client):
    assert (await client.get("/api/metrics")).status_code == 401
    # a session cookie alone is not enough
    assert (await authenticated_client.get("/api/metrics")).status_code == 401
    r = await client.get("/api/metrics", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_non_admin_token_forbidden(client, db_session, make_user):
    member = await make_user("member")
    token, _ = await api_tokens.mint(db_session, user_id=member.id, label="m")
    r = await client.get("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


async def test_metrics_body(client, authenticated_client, db_session, admin_user, redis_url):
    pid = (await authenticated_client.post("/api/printers", json=CREATE)).json()["id"]
    rc = redis_lib.Redis.from_url(redis_url)
    rc.set(state_key(pid), json.dumps({"gcode_state": "RUNNING"}))
    rc.close()
    with base.sync_session() as s:
        s.add(SmartPlug(printer_id=pid, kind="rest", config_enc="x", last_power_w=88.5))
        s.commit()
    token, _ = await api_tokens.mint(db_session, user_id=admin_user.id, label="prom")
    r = await client.get("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert 'tdmm_printers{state="RUNNING"} 1' in body
    assert f'tdmm_plug_power_watts{{printer_id="{pid}"}} 88.5' in body
    for name in ("tdmm_active_jobs", "tdmm_queue_entries", "tdmm_archives", "tdmm_info"):
        assert f"# TYPE {name} gauge" in body
