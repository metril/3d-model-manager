"""Update checker (Q7): latest GitHub release of ``metril/3d-model-manager``
vs the running version, cached in settings (``update_cache``)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.ops_settings import get_ops_config_sync, set_update_cache_sync

RELEASES_URL = "https://api.github.com/repos/metril/3d-model-manager/releases/latest"


def _parse(v: str | None) -> tuple[int, ...] | None:
    m = re.match(r"^v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str | None, current: str | None) -> bool:
    a, b = _parse(latest), _parse(current)
    return a is not None and b is not None and a > b


def check(session: Session, settings: Settings, client: httpx.Client | None = None) -> dict:
    if not get_ops_config_sync(session, settings).update_check_enabled:
        return {"skipped": True}
    own = client is None
    http = client or httpx.Client(timeout=10.0)
    current = settings.app_version
    try:
        r = http.get(RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data = r.json()
        latest = str(data.get("tag_name") or "")
        cache = {
            "latest": latest,
            "current": current,
            "url": data.get("html_url"),
            "notes": (data.get("body") or "")[:4000],
            "update_available": is_newer(latest, current),
            "checked_at": datetime.now(UTC).isoformat(),
            "error": None,
        }
    except (httpx.HTTPError, ValueError, AttributeError):
        cache = {
            "latest": None,
            "current": current,
            "url": None,
            "notes": None,
            "update_available": False,
            "checked_at": datetime.now(UTC).isoformat(),
            "error": "could not fetch releases",
        }
    finally:
        if own:
            http.close()
    set_update_cache_sync(session, cache)
    return cache
