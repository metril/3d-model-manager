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
    # Pinned upstream commit the obico-ml sidecar is built from (compose.yaml
    # sets it from the same pin as compose.obico.yaml). The ML API exposes no
    # version endpoint, so this is the "configured" version shown in Settings.
    # Defaults to the same pin so the card never reads "unknown" when the env
    # isn't forwarded (a test keeps all three in agreement).
    obico_ml_version: str = "6f938964c63892df2b6d5b68d99ea783fc1a7c78"
    database_url: str = "postgresql+asyncpg://tdmm:tdmm@localhost:5432/tdmm"
    # Orca Cloud (cloud preset sync): OAuth device-flow endpoints + the public
    # client id registered for this app. Override ORCA_API_BASE to target
    # Orca's staging API.
    orca_api_base: str = "https://api.orcaslicer.com"
    orca_client_id: str = "oc_app_e873d49ce7dbcc7dca8ba386"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("./data")
    library_root: Path = Path("./library")
    admin_username: str = "admin"
    admin_password: SecretStr | None = None
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
    # Bambu Studio sidecar exchange dir (shared with the studio container as
    # `/exchange`): per-session `<token>/in/` inputs, `<token>/*` returns.
    studio_exchange_dir: Path = Field(
        default=Path("/exchange"), validation_alias=AliasChoices("STUDIO_EXCHANGE_DIR")
    )
    # Printer integration (SPEC "Printer integration"; M4). OFF by default:
    # the whole app is fully functional without it -- the printers API 503s,
    # printerd idles, and the frontend greys the Printer nav.
    printer_enabled: bool = False
    # Overrides the on-disk Fernet key at {data_dir}/secrets/printer.key when
    # set (e.g. to share one key across api/worker/printerd via env instead of
    # a shared volume). A urlsafe-base64 32-byte Fernet key.
    printer_key: SecretStr | None = None

    # seed-only: public URL, upload/download caps, SSE heartbeat, slicer URL/
    # timeout, virtual-printer network, Obico URLs and COOKIE_SECURE moved to
    # the DB-backed `server` settings row (app.services.server_config /
    # app.services.obico). Their env vars are read ONLY by those seed
    # functions, straight from os.environ, so a malformed value can never
    # crash get_settings().


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()
