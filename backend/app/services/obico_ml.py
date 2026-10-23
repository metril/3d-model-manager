"""Obico ML API client (Q10). The ML API is GET-only (``/p/?img=<url>``), so
the JPEG is stashed in Redis under a one-shot nonce and the sidecar fetches it
from the API's public ``/api/obico/frame/{nonce}``. Sync: used from printerd's
scorer thread and (via a worker thread) the API test endpoint."""

from __future__ import annotations

import math
import secrets

import httpx

from app.services.obico import FRAME_NONCE_TTL_S, ObicoConfig, nonce_key

ML_TIMEOUT_S = 8.0  # < camera VIEWER_TTL_S so the viewer heartbeat never lapses mid-call


class MlError(Exception):
    pass


def score_from_detections(detections: list | None) -> float:
    """Sum the confidence column of ``[label, confidence, [x, y, w, h]]``."""
    total = 0.0
    for det in detections or []:
        try:
            value = float(det[1])
        except (IndexError, TypeError, ValueError):
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        total += value
    return total


def score_frame(
    redis_client,
    cfg: ObicoConfig,
    jpeg: bytes,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = ML_TIMEOUT_S,
) -> float:
    """Raises ``MlError`` on any failure (type only -- never echo URLs)."""
    if not cfg.url:
        raise MlError("not_configured")
    nonce = secrets.token_urlsafe(32)
    redis_client.set(nonce_key(nonce), jpeg, ex=FRAME_NONCE_TTL_S)
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            resp = client.get(
                f"{cfg.url}/p/", params={"img": f"{cfg.callback_url}/api/obico/frame/{nonce}"}
            )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise MlError("bad_response")
        return score_from_detections(body.get("detections"))
    except MlError:
        raise
    except Exception as exc:
        raise MlError(type(exc).__name__) from None
    finally:
        redis_client.delete(nonce_key(nonce))
