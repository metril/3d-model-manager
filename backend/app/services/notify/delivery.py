"""Delivery core (Q6): channel config (de)serialization, quiet hours, cron
due-ness, photo lookup and the actual provider send."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import redis
from celery.schedules import crontab_parser

from app.config import Settings, get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models.notify import NotificationChannel
from app.services import camera_state
from app.services.notify import MASK, PROVIDERS, Message, ProviderError


def load_config(settings: Settings, channel: NotificationChannel) -> dict:
    try:
        return json.loads(decrypt_secret(settings, channel.config_enc))
    except Exception:
        return {}


def dump_config(settings: Settings, config: dict) -> str:
    return encrypt_secret(settings, json.dumps(config))


def secret_fields(kind: str) -> set[str]:
    return {f["name"] for f in PROVIDERS[kind].fields if f["secret"]}


def mask_config(kind: str, config: dict) -> dict:
    secrets = secret_fields(kind)
    return {k: (MASK if k in secrets and v else v) for k, v in config.items()}


def merge_config(kind: str, old: dict, new: dict) -> dict:
    """Incoming config over stored; a secret sent back as the mask keeps its value."""
    secrets = secret_fields(kind)
    out = {k: v for k, v in new.items() if k in {f["name"] for f in PROVIDERS[kind].fields}}
    for k in secrets:
        if out.get(k) == MASK:
            if k in old:
                out[k] = old[k]
            else:
                out.pop(k)
    return out


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_quiet_hours(quiet: dict | None, now: datetime | None = None) -> bool:
    if not quiet or not quiet.get("start") or not quiet.get("end"):
        return False
    try:
        tz = ZoneInfo(quiet.get("tz") or "UTC")
        local = (now or datetime.now(UTC)).astimezone(tz)
        cur, start, end = local.hour * 60 + local.minute, _hm(quiet["start"]), _hm(quiet["end"])
    except Exception:
        return False
    if start == end:
        return False
    return start <= cur < end if start < end else (cur >= start or cur < end)


def validate_cron(expr: str) -> bool:
    try:
        _cron_sets(expr)
    except Exception:
        return False
    return True


def _cron_sets(expr: str):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron needs 5 fields")
    minute = crontab_parser(60).parse(parts[0])
    hour = crontab_parser(24).parse(parts[1])
    dom = crontab_parser(31, 1).parse(parts[2])
    month = crontab_parser(12, 1).parse(parts[3])
    dow = {d % 7 for d in crontab_parser(8).parse(parts[4])}
    return minute, hour, dom, month, dow


def cron_due(expr: str, last: datetime, now: datetime) -> bool:
    """True when a minute matching ``expr`` (UTC) lies in ``(last, now]``.
    Lookback is capped at 2 days."""
    minute, hour, dom, month, dow = _cron_sets(expr)
    t = max(last, now - timedelta(days=2)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    while t <= now:
        if (
            t.minute in minute
            and t.hour in hour
            and t.day in dom
            and t.month in month
            and (t.weekday() + 1) % 7 in dow
        ):
            return True
        t += timedelta(minutes=1)
    return False


def fetch_photo(
    printer_id: int | None, payload: dict, settings: Settings | None = None
) -> bytes | None:
    """The kept job frame when the event has a job, else the live frame
    (starts a camera viewer for a few seconds if none is running)."""
    if printer_id is None:
        return None
    settings = settings or get_settings()
    r = redis.Redis.from_url(settings.redis_url)
    try:
        job_id = payload.get("job_id")
        if job_id is not None:
            frame = r.get(camera_state.job_frame_key(printer_id, int(job_id)))
            if frame:
                return frame
        return camera_state.get_frame(r, printer_id) or camera_state.wait_for_frame(
            r, printer_id, timeout=4.0, viewer_id="notify"
        )
    except Exception:
        return None
    finally:
        r.close()


def send_message(
    channel: NotificationChannel,
    msg: Message,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> None:
    """Send via the channel's provider. Raises ``ProviderError``."""
    provider = PROVIDERS.get(channel.kind)
    if provider is None:
        raise ProviderError(f"unknown provider {channel.kind}")
    config = load_config(settings or get_settings(), channel)
    own = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        try:
            provider.send(config, msg, client)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request failed: {type(exc).__name__}") from exc
    finally:
        if own:
            client.close()
