# Spec: Server configuration moves from .env to the web UI

## Context
Infra knobs (slicer, public URL, caps, VP network, Obico URLs) are env-only and need a restart, and the three sidecars are opt-in via compose profiles. Goal: env = bootstrap only; everything else lives in the DB and is edited from an admin "Server" tab. Sidecars always ship in compose; the UI decides whether each one is used.

## Goals / non-goals
- G: one `server` settings key (DB), seed-once from env, stale-env warning (same pattern as `seed_app_config`/`warn_ignored_env`).
- G: `/features` reads only the DB. Cookie `Secure` is derived from the public URL. Log level can change at runtime.
- G: slicer, obico-ml and virtual-printer always-on. Slicer image published as `ghcr.io/metril/3d-model-manager-slicer`.
- G: the VP container idles without binding ports and hot-reloads network changes.
- NG: no moving DATABASE_URL/REDIS_URL/paths/PRINTER_KEY, no settings pub/sub (polling stays), no multi-VP serving, no arm64 slicer.

## Boundary
| Stays env (bootstrap) | Moves to DB/UI (`server` key unless noted) |
|---|---|
| DATABASE_URL, REDIS_URL, DATA_DIR, LIBRARY_ROOT, STATIC_DIR, APP_VERSION, GLTFPACK_PATH | TDMM_PUBLIC_URL → `public_url` |
| PRINTER_KEY | MAX_UPLOAD_BYTES / MAX_DOWNLOAD_BYTES → `max_upload_bytes` / `max_download_bytes` |
| ADMIN_USERNAME/ADMIN_PASSWORD (seed) | SSE_HEARTBEAT_INTERVAL → `sse_heartbeat_s` |
| WATCH_DIR (bind mount) | TDMM_SLICER_URL / TDMM_SLICE_TIMEOUT_S → `slicer_url` / `slice_timeout_s` (+ new `slicer_enabled`) |
| entrypoint ROLE/QUEUES/CONCURRENCY/PUID/PGID/MAX_* | TDMM_VP_BIND/PORT_SSDP/PORT_MQTT/PORT_FTP/PASV_MIN/PASV_MAX/PASV_ADDRESS → `vp_*` |
| compose interpolation (IMAGE_TAG, SLICER_*, OBICO_ML_TAG, TDMM_DB_HOST_PORT, TDMM_REDIS_HOST_PORT) | TDMM_OBICO_URL / TDMM_OBICO_CALLBACK → existing `obico` key (`url`, `callback_url`), seed-once |
| | (new) → `log_level` |
| | COOKIE_SECURE → **removed** (derived) |
| | TDMM_VP_ENABLED → **removed** (always true) |

## `server` key: `backend/app/services/server_config.py`
Separate from `AppConfig` because AppConfig holds `settings:write` feature knobs, while these are admin-only infra knobs with their own seed/meta rules.
```python
class ServerConfig(BaseModel):
    public_url: str | None = None            # controller decision (6): http(s) scheme required, path allowed (subpath deploys), trailing / stripped
    max_upload_bytes: int = <Settings default>   # ge=1MiB
    max_download_bytes: int = <Settings default> # ge=1MiB
    sse_heartbeat_s: int = 15                # 5..120
    log_level: Literal["DEBUG","INFO","WARNING","ERROR"] = "INFO"
    slicer_enabled: bool = True
    slicer_url: str = "http://slicer:3001"
    slice_timeout_s: int = 600               # 30..7200
    vp_bind: str = "0.0.0.0"
    vp_port_ssdp: int = 2021; vp_port_mqtt: int = 8883; vp_port_ftp: int = 990  # 1..65535, distinct
    vp_pasv_min: int = 50000; vp_pasv_max: int = 50009  # min<=max, range ∩ fixed ports = ∅
    vp_pasv_address: str | None = None
```
- API: `get_server_config(db, settings)`, `get_server_config_sync(session, settings)`, `set_server_config(db, cfg)`, `seed_server_config(sync|async)`, `warn_ignored_server_env(settings)`, `seeded_fields(db) -> list[str]`.
- JSONB row `settings.key="server"`, value = fields + `"_seeded_from_env": [field,...]`.
- Resolution: literal default → JSONB overlay. Env is **not** read at resolve time; the env→value mapping lives in `ENV_SEED = {field: env_name}`.

## Seed / precedence / warnings
1. Controller decision (1): EVERY long-lived process (api lifespan after `seed_app_config`, worker `worker_process_init`, `beat_init`, printerd start, VP start) calls `seed_server_config` + `seed_obico_config` at startup. ON CONFLICT DO NOTHING makes this idempotent. Env is read only inside the seed modules. If the `server` row is absent, insert it. For each field: the env value if that env var is explicitly set (`os.environ`), else the legacy `app` row's `slicer_url`/`slice_timeout_s` if present (the old seed stored them), else the literal default. Fields that came from env go into `_seeded_from_env`. Use INSERT … ON CONFLICT DO NOTHING (races between api replicas).
2. Row present: never overwrite. A PUT removes the changed fields from `_seeded_from_env`.
2a. Controller decision (2): an invalid env seed value never crashes. Log a WARNING naming the var and fall back to that field's default, for that field only. Validators normalise where safe (e.g. strip a trailing `/` from `public_url`).
3. `warn_ignored_server_env`: once per startup, a WARNING for each name in `ENV_SEED` plus `COOKIE_SECURE`, `TDMM_VP_ENABLED`, `TDMM_OBICO_URL`, `TDMM_OBICO_CALLBACK` that is still set: "<NAME> is ignored after first boot; edit Settings → Server". The same list is returned by the API as `ignored_env`.
4. Controller decisions (3, 7): Obico gets `seed_obico_config` in `services/obico.py`. It runs in the same startup step as the server seed. Per field, a missing OR empty stored `url`/`callback_url` gets the env value, or else the default (`http://obico-ml:3333` / `http://api:8080`). The resolver applies the same defaults and never reads env. The existing `enabled` flag stays false by default and is the ONLY switch, so nothing turns on by itself. `api/obico.py:146` falls back to the resolved row's `callback_url`, not a literal and not `settings.obico_callback`.
5. `AppConfig` drops `slicer_url`/`slice_timeout_s`. `slicer_client.client_from_config` takes a `ServerConfig`. Controller decision (11): `AppSettingsIn` uses `extra="ignore"`, so clients still sending `slicer_url`/`slice_timeout_s` have them silently dropped. On first read, GET/PUT `/settings/server` also does a one-shot migration of legacy slicer values from the `app` row into `server` when `server` still holds defaults not seeded from env, then removes those keys from the `app` JSONB.
6. The moved fields stay on `Settings` as seed-only sources (`# seed-only` comment). Guard test: AST/grep over `backend/app` fails if `settings.(public_url|cookie_secure|max_upload_bytes|max_download_bytes|sse_heartbeat_interval|slicer_url|slice_timeout_s|obico_url|obico_callback|vp_\w+)` appears outside `services/server_config.py` and `services/obico.py`.

## Consumers (each reads ServerConfig on its existing cadence)
- `api/uploads.py`, `api/imports.py` and download tasks: caps, read per request/task.
- `api/events.py`: heartbeat, read once per stream open.
- `api/files.py`: slicer deep links. `api/oidc.py`: callback base = `public_url or request.base_url` (controller decision 6).
- `api/slicer.py` and slicing tasks: `slicer_enabled`, url, timeout. When disabled → 409 `slicer_disabled`.
- VP process: `vp_*` (below).

## Cookie rule (`api/auth.py`, both `set_cookie` sites)
`secure = public_url.startswith("https://")` if `public_url` is set, else `request.url.scheme == "https"` (uvicorn already gets `--proxy-headers`/forwarded-allow-ips; verify in entrypoint and add if missing). Helper `app/services/server_config.cookie_secure(cfg, request)`. With COOKIE_SECURE=true set and `public_url` unset, the startup warning says to set the Public URL.

## Log level
- `app/logging_config.py`: add `apply_level(name)`, which calls `logging.getLogger().setLevel` and returns early if the level is unchanged. Add `LevelPoller(ttl_s=30)` with `.maybe_apply_sync(session_factory)` and `async .maybe_apply(db_factory)`.
- api: an asyncio task in the lifespan, every 15 s. celery workers: `task_prerun` signal → `maybe_apply_sync` (TTL means at most one DB read per 30 s per process). beat: apply once on `beat_init` (beat logs little; documented). printerd: in its 2.5 s loop via the poller. VP: in its 10 s loop.
- PUT also applies immediately in the handling api process.

## Features (`api/features.py`)
Controller decision (5): `slicer_configured = cfg.slicer_enabled and bool(cfg.slicer_url)`, DB only. Health is NOT folded in. Live health comes from `/settings/server/status`, and the existing `/slicer/health` gating of UI buttons stays. `virtual_printer_enabled: true` stays as a constant for compat. No `Settings` use except `watch_dir` (bootstrap mount). Update the `web/src/api/types.ts` Features comment.

## Sidecars / compose
- `compose.yaml`: `include: [compose.slicer.yaml, compose.obico.yaml]`. Remove `profiles` from slicer, obico-ml and virtual-printer. Remove `TDMM_VP_ENABLED` from api env.
- VP stays `network_mode: host` (SSDP discovery). Fresh deploys must reach db/redis with zero config: `db.ports: ["127.0.0.1:${TDMM_DB_HOST_PORT:-15432}:5432"]`, `redis.ports: ["127.0.0.1:${TDMM_REDIS_HOST_PORT:-16379}:6379"]`. Controller decision (10): VP gets `environment: DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-tdmm}:${POSTGRES_PASSWORD:-tdmm}@127.0.0.1:${TDMM_DB_HOST_PORT:-15432}/${POSTGRES_DB:-tdmm}` and `REDIS_URL: redis://127.0.0.1:${TDMM_REDIS_HOST_PORT:-16379}/0`, overriding env_file. The db service's hardcoded creds become `${POSTGRES_USER:-tdmm}`/`${POSTGRES_PASSWORD:-tdmm}`/`${POSTGRES_DB:-tdmm}` (healthcheck too), so one source feeds both.
- `compose.slicer.yaml`: `image: ${SLICER_IMAGE:-tdmm-slicer:local}` with `build: ./slicer`. `compose.prod.yaml` adds `slicer: image: ghcr.io/metril/3d-model-manager-slicer:${IMAGE_TAG:-latest}`, `build: !reset null`, `pull_policy: always`. obico-ml and virtual-printer get the same prod treatment (VP = app image).
- `.github/workflows/release.yml` `slicer-image` job: after the smoke test, push `ghcr.io/metril/3d-model-manager-slicer` tags `:edge`, `:sha-<short>`, `:<bambu>-<sha>`. When `release_created`, also push `:X.Y.Z`, `:X.Y`, `:X`, `:latest`. amd64 only. Drop the old `tdmm-slicer` name. Add `needs.release-please` outputs to `env`.
- `mem_limit` stays at 4g (accepted).

## Virtual printer idle + reload (`app/virtual_printer/__main__.py`, `service.py`)
- Controller decision (9): if the DB read fails, write `{state:"error", errors:[detail]}` with the same 30 s TTL. If Redis itself is unreachable, nothing is written and the UI shows `offline` (intended).
- Remove the startup `preflight` exit. Each 10 s poll loads `wanted = (VpConfig | None, VpNetwork)`, where `VpNetwork` is a frozen dataclass of `vp_*` read via `get_server_config_sync`.
- `wanted.vp is None` → no listeners bound, status `idle`.
- `wanted != current` → stop the old service, then `preflight(net)`. On problems: status `error` with details, `current=None` so the next poll retries, process keeps running. Otherwise start `VirtualPrinterService(settings, vp, net)` → status `running`.
- `service.py`/`preflight`: take `VpNetwork` instead of `settings.vp_*`.
- Status: `SET tdmm:vp:status <json> EX 30` every poll. JSON: `{state: idle|running|error, vp_id, bind, ports:{ssdp,mqtt,ftp}, pasv:[min,max], errors:[str], ts}`. If the key is missing, the API reports `state: "offline"`.

## API (`backend/app/api/server_settings.py`, prefix `/settings/server`, `require_perm("admin")` on the router, like `admin_ops` admin routes)
- `GET /api/settings/server` → `ServerSettingsOut`
- `PUT /api/settings/server` body `ServerSettingsIn` (= ServerConfig, full object) → `ServerSettingsOut`; writes an audit_log entry (as other settings PUTs do).
- `POST /api/settings/server/slicer/test` body `{url?: str}` → `{ok: bool, latency_ms: int|null, detail: str}`; GET `<url or cfg.slicer_url>/health` with a 5 s timeout.
- `GET /api/settings/server/status` → `{slicer:{ok,detail}, slicer_healthy: bool|null, obico:{ok,detail}, virtual_printer:{state,vp_id,bind,ports,pasv,errors,ts}}`. Slicer is probed live via `/health` (3 s timeout). `slicer_healthy` is null when the slicer is disabled or has no URL (controller decision 5). Controller decision (8): obico probe = GET `{url}/hc/`, ok on 200, 2 s timeout (matches the compose healthcheck). Probe results are cached 10 s in Redis `tdmm:server:probe`.
- `ServerSettingsOut = {config: ServerConfig, seeded_from_env: {field: ENV_NAME}, ignored_env: [ENV_NAME], derived: {cookie_secure: bool|null, request_origin: str}}`. `cookie_secure` is null when it depends on the request.
- Schemas go in `schemas/settings.py` (`ServerSettingsIn/Out`, `SlicerTestIn/Out`, `ServerStatusOut`). No new tables, so no `created_by_user_id`.

## UI (admin-only tab `server` in `web/src/pages/SettingsPage.tsx`)
`web/src/api/serverSettings.ts` (react-query hooks). Components in `web/src/components/settings/server/`:
- `PublicUrlCard`: input; amber warning when the value differs from `window.location.origin`; "Use current origin" button; derived cookie Secure line.
- `LimitsCard`: upload/download caps (MB input ↔ bytes), SSE heartbeat, log level select.
- `SlicerServerCard`: enabled switch, URL, timeout, health badge (status endpoint, refetch every 15 s), "Test" button.
- `ObicoServerCard`: url/callback via the existing `GET/PUT /settings/obico` (full-object round-trip). `FailureDetectionCard` drops those inputs and links to the Server tab.
- `VpNetworkCard`: bind, 3 ports, pasv min/max/address, status badge + errors from the status endpoint.
- `SeededHint`: shows "Seeded from environment (ENV_NAME)" per field.
- Printer tab: `VirtualPrinterCard` always rendered (drop the features gate). Strings go in `web/src/i18n/locales/en.json` (`settings.server.*`).

## Migration / compat
- No alembic migration (JSONB key only).
- An existing deployment with TDMM_SLICER_URL set: first boot seeds `slicer_url` from env and `slicer_enabled=true`. It keeps working with no action, and logs a warning to remove the env var.
- An existing `app` row's `slicer_url`/`slice_timeout_s` is carried over if the env var is unset. Leftover keys in the `app` JSONB are ignored.
- Deployments that had no slicer get the sidecar on upgrade and `slicer_enabled=true`. The admin can switch it off in the UI.
- COOKIE_SECURE=true deployments behind a TLS proxy: covered by forwarded headers. The warning prompts setting the Public URL.
- VP: the old preflight exit is gone. The host ports 15432/16379 (loopback) are newly bound and can be overridden via TDMM_DB_HOST_PORT/TDMM_REDIS_HOST_PORT.

## Testing
- Unit (`tests/test_server_config.py`): resolve/defaults, seed from env, seed from legacy app row, no overwrite, `_seeded_from_env` pruned on PUT, ignored-env warning (caplog), validation (ports distinct, pasv range).
- `test_features_api.py`: DB-only. Cookie derivation (3 cases) in `test_auth.py`. Log-level poller TTL + apply.
- Guard test for seed-only Settings fields.
- Controller decision (4): test fixtures seed `slicer_enabled=false` wherever existing tests assume "no slicer". The production default stays true.
- Invalid env seed → warning + field default. Seed runs idempotently from multiple processes. Obico empty-url reseed. Legacy app→server one-shot migration. `AppSettingsIn` ignores extra keys.
- `test_obico_api.py`: seed + no env fallback.
- VP (`test_virtual_printer.py`): idle binds nothing, port conflict → error status and the process does not exit, network change → restart, status key written.
- Perm/auth sweeps: the new routes must be admin-gated; non-admin gets 403 in `test_perm_sweep`/`test_perm_matrix`/`test_auth_sweep` (update the expectation tables).
- Web: vitest for each card + `SettingsPage.test.tsx` (tab only for admin, VP card always shown). `npm run build` + oxlint baseline.
- e2e: `scripts/e2e.sh` stops editing `PRINTER_ENABLED` in `.env` and calls `PUT /api/settings/app` after login. It brings up an explicit core service list (`db redis api worker-io worker-cpu beat printerd`) so the sidecars do not slow CI. `.github/workflows/e2e.yml` gets matching edits.

## Docs
- `.env.example`: delete the moved vars, keep a "first-boot seed only" block listing them.
- README: quickstart (sidecars on by default, Server tab), slicer section (published image, no env), PRINTER_KEY section unchanged, VP section (loopback db/redis ports).
- Header comments in the compose files. `web/src/api/types.ts` Features comment.

## Risks → decisions
- A wrong public URL breaks the OIDC callback and Secure cookies → mismatch warning + "Use current origin" button. The OIDC callback falls back to the request origin when unset.
- Locking yourself out with https public URL over http → cookie Secure follows public URL; the warning flags the http origin mismatch before save (confirm dialog).
- VP port conflicts → error status in VpNetworkCard; the process keeps retrying and never crash-loops.
- Always-on obico-ml and slicer cost RAM/disk (the ml image is large) → documented. Disabling in the UI stops their use, not the containers; operators can `docker compose stop` them.
- A stale env var silently ignored → startup warning + `ignored_env` banner in the UI.
- Multiple api replicas seeding at once → ON CONFLICT DO NOTHING.
