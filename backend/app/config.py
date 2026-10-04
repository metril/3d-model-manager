"""Application configuration.

Settings are loaded from unprefixed environment variables (e.g.
``DATABASE_URL``); Round 9 dropped the old app prefix and the unit
suffixes from the env names. Field names KEEP their unit suffixes (e.g.
``scan_interval_s``) and bridge to the suffix-less env vars via per-field
``validation_alias``. See the M1 plan's Global Constraints for the full
list of supported variables.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the tdmm backend."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # Version stamp baked into the image by docker/Dockerfile's `ARG
    # APP_VERSION` (CI sets the release-please semver on release builds,
    # `edge-<short sha>` otherwise). `dev` is what an unstamped image or a
    # bare local `uvicorn app.main:app` reports. Surfaced on
    # FastAPI(version=...) -- so it lands in /api/openapi.json and the /docs
    # header -- and in the unauthenticated GET /api/health payload, the one
    # endpoint an operator can hit to answer "what is actually running?"
    # without a session.
    app_version: str = "dev"
    database_url: str = "postgresql+asyncpg://tdmm:tdmm@localhost:5432/tdmm"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Field(
        default=Path("./data"),
        validation_alias=AliasChoices("DATA_DIR", "TDMM_DATA_DIR", "data_dir"),
    )
    library_root: Path = Field(
        default=Path("./library"),
        validation_alias=AliasChoices(
            "LIBRARY_ROOT", "LIBRARY_DIR", "TDMM_LIBRARY_ROOT", "library_root"
        ),
    )
    admin_username: str = "admin"
    admin_password: SecretStr | None = None
    # NOTE: leave False only for plain-http dev/tests; set COOKIE_SECURE=true
    # whenever the app is served over https (public_url starts with https://).
    cookie_secure: bool = False
    # Byte caps on streamed ingest: PUT /api/uploads + slicer intake
    # (`MAX_UPLOAD_BYTES`, default 4 GiB) and remote importer downloads
    # (`MAX_DOWNLOAD_BYTES`, default 2 GiB). Exceeding them aborts the stream.
    max_upload_bytes: int = Field(
        default=4 * 1024**3, validation_alias=AliasChoices("MAX_UPLOAD_BYTES")
    )
    max_download_bytes: int = Field(
        default=2 * 1024**3, validation_alias=AliasChoices("MAX_DOWNLOAD_BYTES")
    )
    # How often GET /api/events sends a `: ping` heartbeat comment while idle
    # (Task 6). Overridable so tests don't have to wait a real 15s.
    # Env: `SSE_HEARTBEAT_INTERVAL` (seconds; the field keeps the unit suffix).
    sse_heartbeat_interval_s: float = Field(
        default=15.0, validation_alias=AliasChoices("SSE_HEARTBEAT_INTERVAL")
    )
    # Directory the built frontend (`web/dist`) lives in, e.g. `/app/static`
    # inside the Docker image (Task 9). `None` (the default) disables SPA
    # serving entirely -- local dev runs the Vite dev server instead, which
    # proxies `/api` to this backend (see README "Development").
    static_dir: Path | None = None
    # Path/name of the gltfpack executable (M2 "Processing pipeline":
    # `optimize_glb`/browser meshopt compression). Defaults to whatever
    # `gltfpack` resolves to on PATH (see tests/conftest.py, which prepends
    # the npm-installed WASM shim's bin dir for local dev/CI).
    gltfpack_path: str = "gltfpack"
    # Opt-in scheduled scan (SPEC "optional scheduled scan"; Task 5 brief):
    # seconds between automatic `scan_library` runs via Celery beat. `0`
    # (the default) means OFF -- see `app.tasks.celery_app`'s conditional
    # `beat_schedule`. Env: `SCAN_INTERVAL` (seconds).
    scan_interval_s: int = Field(default=0, validation_alias=AliasChoices("SCAN_INTERVAL"))
    # Opt-in periodic sync of followed remote collections/favourites (M8 H):
    # seconds between automatic `sync_collections.sync_all` runs via Celery
    # beat. `0` (the default) means OFF -- the "Sync now" button still works.
    # Env: `COLLECTION_SYNC_INTERVAL` (seconds).
    collection_sync_interval_s: int = Field(
        default=0, validation_alias=AliasChoices("COLLECTION_SYNC_INTERVAL")
    )
    # Watched-folder auto-import (Round 8 Task 5): a directory a slicer can
    # export finished sliced files straight into, polled periodically by
    # Celery beat and resolved to a model the same way
    # `POST /api/slicer/intake` does (`app.services.slicer_intake
    # .resolve_and_attach_sync`, via `app.tasks.slicer_watch`). `None` (the
    # default) leaves the feature entirely off.
    watch_dir: Path | None = None
    # Seconds between watch-dir polls via Celery beat. `0` (the default)
    # means OFF, mirroring `scan_interval_s`/`collection_sync_interval_s` --
    # BOTH this and `watch_dir` must be set for the beat entry to
    # fire (see `app.tasks.celery_app`'s conditional `beat_schedule`).
    # Env: `WATCH_INTERVAL` (seconds).
    watch_interval_s: int = Field(default=0, validation_alias=AliasChoices("WATCH_INTERVAL"))
    # A watched file's mtime must be at least this many seconds in the past
    # before `app.tasks.slicer_watch` will import it -- guards against
    # importing a slicer export that's still being written mid-poll.
    # Env: `WATCH_STABLE` (seconds).
    watch_stable_s: float = Field(default=10.0, validation_alias=AliasChoices("WATCH_STABLE"))
    # Printer integration (SPEC "Printer integration"; M4). OFF by default:
    # the whole app is fully functional without it -- the printers API 503s,
    # printerd idles, and the frontend greys the Printer nav.
    printer_enabled: bool = False
    # Overrides the on-disk Fernet key at {data_dir}/secrets/printer.key when
    # set (e.g. to share one key across api/worker/printerd via env instead of
    # a shared volume). A urlsafe-base64 32-byte Fernet key.
    printer_key: SecretStr | None = None
    # Externally-reachable origin (scheme + host, e.g.
    # `https://models.example.com`) to use for absolute URLs this API mints
    # for outside consumers -- currently just the signed slicer-deep-link
    # URL from `POST /files/{id}/slicer-link` (`app.api.files
    # ._absolute_origin`). Unset (the default) falls back to the request's
    # own `request.base_url`, which does NOT read `X-Forwarded-*` headers
    # (those are client-controllable and untrusted at this layer) -- a
    # reverse proxy in front of the API should be handled by uvicorn's
    # `--proxy-headers`/`--forwarded-allow-ips`, not by this app trusting
    # forwarded headers itself. Env: `TDMM_PUBLIC_URL`.
    public_url: str | None = Field(default=None, validation_alias=AliasChoices("TDMM_PUBLIC_URL"))


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()
