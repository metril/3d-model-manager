# Plan: Settings → Server (env to UI)
Spec: `spec-settings.md` (same dir). Base: `origin/main`. Four parallel Sonnet implementers (A–D), one git worktree each, branches `s1-a-backend`, `s1-b-web`, `s1-c-compose`, `s1-d-vp`. Agents never stash or checkout in a shared tree. Cap pytest runs with `timeout`.

## API contract (frozen; B mocks it until A merges)
```
GET  /api/settings/server            (admin) -> ServerSettingsOut
PUT  /api/settings/server            (admin) body ServerConfig -> ServerSettingsOut | 422
POST /api/settings/server/slicer/test (admin) body {url?: string} -> {ok: bool, latency_ms: number|null, detail: string}
GET  /api/settings/server/status     (admin) -> ServerStatusOut
ServerConfig = {public_url: string|null, max_upload_bytes: int, max_download_bytes: int,
  sse_heartbeat_s: int, log_level: "DEBUG"|"INFO"|"WARNING"|"ERROR",
  slicer_enabled: bool, slicer_url: string, slice_timeout_s: int,
  vp_bind: string, vp_port_ssdp: int, vp_port_mqtt: int, vp_port_ftp: int,
  vp_pasv_min: int, vp_pasv_max: int, vp_pasv_address: string|null}
ServerSettingsOut = {config: ServerConfig, seeded_from_env: {[field]: ENV_NAME},
  ignored_env: ENV_NAME[], derived: {cookie_secure: bool|null, request_origin: string}}
ServerStatusOut = {slicer: {ok: bool, detail: string}, slicer_healthy: bool|null, obico: {ok: bool, detail: string},
  virtual_printer: {state: "idle"|"running"|"error"|"offline", vp_id: int|null, bind: string|null,
  ports: {ssdp,mqtt,ftp}|null, pasv: [int,int]|null, errors: string[], ts: string|null}}
Redis: tdmm:vp:status (JSON = virtual_printer object minus "offline", EX 30); written by D, read by A.
/features: slicer_configured from DB; virtual_printer_enabled always true.
```
Shared seam between A and D: `app/services/server_config.py` (`ServerConfig`, `get_server_config_sync`), `VpNetwork` (`app/virtual_printer/intake.py`), `logging_config.LevelPoller`. **A owns these files.** D codes against the contract above. If D needs them before A merges, D adds a minimal stub, and on rebase drops the stub in favour of A's version.

## A: backend core (`s1-a-backend`)
Files: `backend/app/services/server_config.py` (new), `services/app_config.py`, `services/obico.py`, `services/slicer_client.py`, `config.py`, `logging_config.py`, `main.py`, `api/server_settings.py` (new), `api/__init__.py`, `api/features.py`, `api/auth.py`, `api/oidc.py`, `api/files.py`, `api/obico.py`, `api/uploads.py`, `api/events.py`, `api/slicer.py`, the upload/download/slice tasks, `tasks/celery_app` signals, printerd loop, `schemas/settings.py`, tests.
Tasks:
- Controller decisions 1/2/6: seed is called from every long-lived process start (api lifespan, `worker_process_init`, `beat_init`, printerd; D wires VP). Bad env value → warning + field default. `public_url` requires http(s), allows a path, strips trailing /.
- Write `ServerConfig` + validators, the `ENV_SEED` map, get/get_sync/set, and seed (ON CONFLICT DO NOTHING, legacy `app` row carry-over, `_seeded_from_env`). Add `warn_ignored_server_env` and the `cookie_secure(cfg, request)` helper.
- Controller decisions 3/7: `seed_obico_config` fills missing/empty url/callback from env or the default; `enabled` stays false by default; the resolver never reads env. `api/obico.py:146` uses the resolved `callback_url`.
- Remove `slicer_url`/`slice_timeout_s` from `AppConfig`. Point `slicer_client` and the slicing call sites at ServerConfig. Return 409 `slicer_disabled` when disabled. Controller decision 11: `AppSettingsIn` gets `extra="ignore"`, plus a one-shot app→server legacy slicer migration on first GET/PUT `/settings/server`.
- Switch every consumer of caps, heartbeat, public_url, cookie_secure and slicer settings to ServerConfig.
- Mark the moved `Settings` fields `# seed-only`. Write the guard test (regex scan of `backend/app`).
- `logging_config.apply_level` + `LevelPoller(ttl_s=30)`. Wire it into the api lifespan task (15 s), celery `task_prerun`, `beat_init`, and the printerd loop.
- Write the `api/server_settings.py` router (`require_perm("admin")`): GET/PUT (audit log, immediate `apply_level`), slicer test, and status (slicer `/health` 3 s + `slicer_healthy`; obico `{url}/hc/` 200 within 2 s, controller decision 8; `tdmm:vp:status`; 10 s Redis probe cache). `/features` excludes health (controller decision 5).
- Switch `features.py` to DB-only.
- Check uvicorn `--proxy-headers` in `docker/entrypoint*`; add it if missing.
- Update `test_perm_sweep`/`test_perm_matrix`/`test_auth_sweep` tables for the new routes.
Tests (controller decision 4: fixtures set `slicer_enabled=false` where tests assume no slicer): `tests/test_server_config.py` (new), `test_features_api.py`, `test_auth.py` (cookie, 3 cases), `test_obico_api.py`, `test_app_config_api.py`, log poller test, guard test. Run `cd backend && timeout 1200 uv run pytest -q` and `uv run ruff check .`.
Done when: full pytest is green, ruff is clean, the sweeps cover the new routes, and no `settings.<moved>` reads remain outside the seed modules.

## B: web (`s1-b-web`)
Files: `web/src/api/serverSettings.ts` (new), `web/src/api/types.ts`, `web/src/api/features.ts`, `web/src/pages/SettingsPage.tsx` (+test), `web/src/components/settings/server/{PublicUrlCard,LimitsCard,SlicerServerCard,ObicoServerCard,VpNetworkCard,SeededHint}.tsx` (+tests), `FailureDetectionCard.tsx` (+test), `VirtualPrinterCard` usage, `web/src/i18n/locales/en.json`.
Tasks:
- Write the types and react-query hooks for the four routes from the contract. The status query refetches every 15 s.
- Add an admin-only `server` tab trigger + content (`isAdmin`).
- `PublicUrlCard`: origin-mismatch warning, "Use current origin" button, confirm before saving a mismatched https/http value, derived cookie line.
- `LimitsCard`: MB↔bytes conversion, heartbeat, log-level select.
- `SlicerServerCard`: enable switch, URL, timeout, health badge from `slicer_healthy`; existing `/slicer/health` button gating unchanged (controller decision 5), Test button with toast.
- `ObicoServerCard`: url + callback via the existing obico hooks (full-object PUT). Remove those inputs from `FailureDetectionCard` and add a link to the Server tab.
- `VpNetworkCard`: fields + client-side validation (distinct ports, pasv range) + status badge/errors.
- `SeededHint` on every field. Render an `ignored_env` banner at the top of the tab.
- Render `VirtualPrinterCard` unconditionally on the printer tab. Update the Features type comment (`virtual_printer_enabled` always true).
- Follow the R12 TooltipProvider and "no dead controls" rules. Put all strings in i18n.
Tests: vitest for each card (mocked fetch), plus `SettingsPage.test.tsx` (tab hidden for non-admin, VP card always shown). Run `npm test`, `npm run build`, oxlint baseline.
Done when: tests, build and lint are green, and the UI works against mocked contract fixtures.

## C: compose / CI / docs (`s1-c-compose`)
Files: `compose.yaml`, `compose.slicer.yaml`, `compose.obico.yaml`, `compose.prod.yaml`, `.github/workflows/release.yml`, `.github/workflows/e2e.yml`, `scripts/e2e.sh`, `.env.example`, `README.md`.
Tasks:
- `compose.yaml`: include both sidecar files, remove all `profiles`, remove `TDMM_VP_ENABLED`. Add loopback `ports` on db/redis (`TDMM_DB_HOST_PORT:-15432`, `TDMM_REDIS_HOST_PORT:-16379`). Add VP `environment` DATABASE_URL/REDIS_URL built from `${POSTGRES_USER:-tdmm}:${POSTGRES_PASSWORD:-tdmm}@127.0.0.1:${TDMM_DB_HOST_PORT:-15432}/${POSTGRES_DB:-tdmm}`. Switch the db service creds and healthcheck to the same `${POSTGRES_*}` interpolations (controller decision 10). Document `POSTGRES_*` in `.env.example`.
- `compose.slicer.yaml`: `image: ${SLICER_IMAGE:-tdmm-slicer:local}` and refresh the header. `compose.obico.yaml`: drop the profile and update the header.
- `compose.prod.yaml`: add slicer (`ghcr.io/metril/3d-model-manager-slicer:${IMAGE_TAG:-latest}`, `build: !reset null`, `pull_policy: always`) and virtual-printer (app image).
- `release.yml` `slicer-image`: push `3d-model-manager-slicer` with `:edge`, `:sha-<short>`, `:<bambu>-<sha>`. When `release_created`, also push `:X.Y.Z`, `:X.Y`, `:X`, `:latest`. Remove the old `tdmm-slicer` name.
- `scripts/e2e.sh`: explicit core service list on `up`. Replace the `PRINTER_ENABLED` `.env` edit with a `PUT /api/settings/app` call after login. Apply matching edits to `e2e.yml`.
- `.env.example`: remove the moved vars, add a "first-boot seed only (ignored after; edit Settings → Server)" block, remove COMPOSE_PROFILES guidance for the sidecars.
- README: quickstart, Server tab, slicer image, VP loopback ports, resource note for always-on sidecars.
- Validate with `docker compose config -q` for base and base+prod. Lint workflows with `actionlint` if available.
Done when: compose config validates for both, the e2e script passes `bash -n` with no grep of removed vars, and the docs have no stale env instructions.
Push note: `.github/workflows/*` changes need SSH push (the gh token lacks `workflow` scope). Flag this to the controller.

## D: virtual printer (`s1-d-vp`)
Files: `backend/app/virtual_printer/__main__.py`, `service.py`, `intake.py` (`VpNetwork`), `backend/tests/test_virtual_printer.py`.
Tasks:
- Add `VpNetwork` frozen dataclass + `load_network(settings)` (via `get_server_config_sync`).
- Make `preflight(net)` and `VirtualPrinterService(settings, vp, net)` use `net.*` instead of `settings.vp_*`.
- Remove the startup preflight exit. In the loop, `wanted=(vp, net)`. Stop/preflight/start on change. On preflight failure: error status, `current=None`, keep looping.
- Call `seed_server_config`/`seed_obico_config` at startup (controller decision 1).
- Controller decision 9: if the DB read fails, write `{state:"error", errors:[detail]}` EX 30. If Redis is down, write nothing (UI shows offline).
- Write `tdmm:vp:status` (EX 30) every poll using the sync redis client from `settings.redis_url`.
- Call `LevelPoller.maybe_apply_sync` each poll.
- Make `main()` use `logging_config.configure_logging` instead of `basicConfig`.
Tests: idle binds no sockets; port conflict → `error` status and no exit; network change → service restarted with new ports; enabling a row → `running`; status JSON shape matches the contract. `timeout 600 uv run pytest -q tests/test_virtual_printer.py`.
Done when: VP tests are green, a fresh DB with no VP row stays idle with zero bound ports, and there are no `settings.vp_` reads left.

## Dependencies / merge order
1. **A** first (it owns the seams).
2. **D** rebases on A, drops any stubs, and runs the full VP suite.
3. **B** rebases on A and swaps mocks for the real endpoints (types already match).
4. **C** last (docs reference the final behaviour; workflow push over SSH).

Alternative: land all four on one integration branch `s1-settings` and open a single PR.
Review: **one** whole-branch Opus review per PR (no per-task reviews). Fixes go into the same branch, then merge after green CI.
