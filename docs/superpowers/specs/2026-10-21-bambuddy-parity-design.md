<!-- Approved 2026-10-21. Roadmap for porting bambuddy's printing features; reimplemented, no bambuddy code. -->
# Bambuddy-parity roadmap for 3d-model-manager

## Context

The user wants the printing-side features of [bambuddy](https://github.com/maziggy/bambuddy) (AGPL-3.0, Bambu-only, SQLite-first, ~4.6k commits) in this project. Decision made in this session: **do not fork bambuddy; port the ideas into this repo, reimplemented (no code copied).** Reasons: AGPL would attach to everything; this repo's foundations (Postgres + Celery, multi-backend storage with revisions, PrinterAdapter seam, three gallery importers, API-token plane, Chrome extension, ~1,200 backend + 96 web test files, R12/R13 studio UI) don't transplant into their schema; carrying their tree is a permanent upstream tax.

Features the user chose: print archive + history, live camera + timelapse, spool inventory, print profiles + **Bambu Studio** slicer sidecar, slicer recipes + multi-printer dispatch, cost tracking, Home Assistant **both** directions, **multi-user with bambuddy-style groups** (per-printer access, job review, cost centers/budgets), **OIDC login (Authentik first, generic OIDC too)**, and a UI/UX overhaul (printer-first dashboard as landing, nav reorganized around printing, one-modal print flow, denser/more polished visuals).

Verified facts: host is x86_64; vendored `bambulabs_api` ships `camera_client.py` (A1 chamber-image JPEG stream on TCP 6000, not RTSP) but `printers/bambu.py` deliberately never starts it because the vendored `PrinterCamera.stop()` hangs when the thread never ran; `prints` is a manual log and finished `print_jobs` never create history; `print_queue` is a per-model list with no printer/dispatcher; `services/spool.py` is the upload staging folder (name collision, avoid); no integrations exist beyond SSE. Auth today: exactly one user (`users`/`sessions` in `models/auth.py`, bootstrapped from `ADMIN_USERNAME`/`ADMIN_PASSWORD` by `services/bootstrap.py`), `require_session` at `protected_router` level in `api/__init__.py`, `api_tokens` not tied to a user, **no domain table has an owner column**, no authlib/OIDC dependency. Alembic head `f3a1c9d2e8b7`.

Ground rules (from memory): Sonnet implements, Opus reviews once per branch (whole-branch), controller coordinates only, TDD, one Alembic migration per schema change, settings in the `app` row via `services/app_config.py`, env names short with `TDMM_` prefix, commit as metril. `compose.yaml` has an uncommitted 1-line local change — leave it alone.

## Phase order

| # | Phase | Depends on | Why |
|---|---|---|---|
| P0 | Commit the design spec | — | `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` = §P1–P8 + §UI of this file, so later sessions can plan from it. |
| P1 | Multi-user, groups, permissions, OIDC | — | Every later table needs `created_by_user_id`; every later route needs a permission name; nav needs role gating. |
| P2 | Print archive, run history, cost v1 | P1 | Spools, cost, reprint, HA energy, billing and dashboard all need a finished-run record. |
| P3 | IA/shell overhaul + printer-first dashboard | P1, P2 | Later features land in the new nav instead of moving twice. |
| P4 | Live camera + timelapse | P2, P3 | Camera tile + archive media; also passes the `timelapse` flag `start_print` never gets. |
| P5 | Spool inventory, AMS mapping, one-modal Print flow | P2, P3 | Per-tray grams from the archive drive spool usage; modal's AMS step needs spools. |
| P6 | Bambu Studio slicer sidecar, profiles, recipes, multi-printer dispatch | P5 | Adds the Profile step and a queue dispatcher. |
| P7 | Job review + cost centers/budgets/ledger | P5, P6 | Needs spool prices and the dispatcher to gate. |
| P8 | Home Assistant both ways, energy cost, hold-on-alert | P2, P6 | Energy lands on run rows; hold gates the dispatcher. |

Each phase = its own branch, independently mergeable, green gates, one Opus whole-branch review. New adapter capabilities = optional ABC methods on `PrinterAdapter` raising `NotImplementedError`; `printers/fake.py` implements them.

**Integration rule for P2–P8 (from P1):** every new table holding something a user creates gets `created_by_user_id` (nullable FK, `ON DELETE SET NULL`) filled from `ctx.user`; Celery/beat tasks write NULL. Use only the central permission enum: archive + slicer intake → `library:write`; spools → `inventory:write`; camera view, pause/stop, AMS actions → `printers:control` per printer; queueing/dispatch → `printers:print` + P7 review state; HA/integration config → `settings:write`. `/api/printers/overview`, SSE printer events, camera streams and queue listings return only printers visible via the shared `visible_printer_ids(ctx)` helper (admins see all). The dispatcher re-checks the creator still has `can_print` on the target before sending. API tokens act as their owner with a capped permission set.

## P1 — Multi-user, groups, permissions, OIDC

- **Schema (one migration):** `users` + `email` (citext unique nullable), `display_name`, `is_active`, `role` admin|member|viewer, `oidc_issuer`+`oidc_subject` (unique together), `last_login_at`; `password_hash` nullable. New `groups(name unique, description, permissions text[])`, `group_members(group_id, user_id, source local|oidc)`, `printer_access(group_id, printer_id, can_print, can_control, requires_review)`, `oidc_providers(name, slug unique, discovery_url, client_id, client_secret_enc Fernet, scopes default "openid profile email groups", groups_claim default "groups", group_mapping JSONB, admin_group, auto_create_users, link_by_email, enabled)`. `api_tokens.user_id` FK cascade. `created_by_user_id` added now to `models`, `revisions`, `files`, `notes`, `collections`, `imports`, `print_queue`, `prints`, `print_jobs` (never on derived/cache tables). Data step: existing user → admin; tokens + rows backfilled to the admin. `ensure_admin_user` stays and upserts `role=admin`.
- **Permissions:** `admin`, `library:write`, `inventory:write`, `printers:print`, `printers:control`, `queue:review`, `settings:write`, `billing:manage`. Role base sets: viewer none; member library/inventory/printers:print; admin all. Effective = role set ∪ groups' `permissions`. Per-printer: OR of `printer_access` across the user's groups (admins full).
- **Enforcement (`api/deps.py`):** `require_session` loads groups + perms once into `AuthContext(user, session, perms, printer_acl)`; `require_perm(*p)` factory → 403, applied at router level like today (settings write router → `settings:write`, inventory → `inventory:write`, `/admin/*` → `admin`); mutating library routes get a per-route `Depends(require_perm("library:write"))`; printer/queue routes call `ctx.require_printer(id, "print"|"control")`. **Library visibility: one shared library; `created_by` is attribution only ("added by"); only admins or the creator delete.**
- **OIDC (authlib):** `GET /auth/oidc/providers` (public), `/auth/oidc/{slug}/start` (state+nonce+PKCE in Redis `oidc:state:{state}`, 10-min TTL, one-shot, `return_to`), `/auth/oidc/{slug}/callback` (code exchange, ID-token validation iss/aud/nonce/exp, userinfo merge). Resolution: `(issuer,sub)` → else `link_by_email` && `email_verified` → else `auto_create_users` (JIT, role member) → else `/login?error=not_provisioned`. Group sync replaces `source=oidc` memberships via `group_mapping`; `admin_group` claim → admin. Inactive users rejected. Reuses `sessions`/cookie. Authentik: `groups` claim, discovery `https://auth.example/application/o/<slug>/.well-known/openid-configuration`, require `email_verified`. Generic: configurable dotted claim path (e.g. Keycloak `realm_access.roles`). Password login stays, refused when `password_hash` is NULL.
- **API:** `/auth/me` → `{id, username, display_name, email, role, permissions[], groups[], has_password}`; `/admin/users` (CRUD, reset password, deactivate → delete sessions), `/admin/groups` (+members), `PUT /admin/groups/{id}/printer-access`, `/admin/oidc-providers` (secret write-only); `/settings/api-tokens` scoped to current user (admins see all).
- **Web:** `LoginPage` provider buttons + local form + `?error=` banner; `useAuth()` exposes `can(perm)`, `canPrinter(id, kind)`; `NavItem.perm` filtered beside `featureGated`; Settings cards Users, Groups (members + printer-access matrix), OIDC providers; write buttons gated by `can()` (server still enforces).
- **Tests:** auth sweep still passes (`_PATH_PARAM_VALUES` += user_id, group_id, provider_id, slug) + a second sweep as `viewer` asserting 403 on every mutating route except an allowlist (this is the CI guard for P2–P8); perm matrix role×group×route; fake IdP via respx (discovery, JWKS w/ test RSA key, token, userinfo): happy path, bad nonce, reused/expired state, unverified email, email link, JIT off, group mapping + admin promotion; migration up/down/up w/ backfills.
- **Live-verify (Authentik):** redirect URI `https://<host>/api/auth/oidc/authentik/callback`, confidential client, scopes `openid profile email groups`; mapped-group user shows admin in `/auth/me`; removing from group drops admin on next login.

## P2 — Print archive + run history + cost v1

- **Schema:** `print_jobs.file_id` nullable + `origin` app|external. New `print_archives(print_job_id FK unique, model_id/revision_id nullable, archive_blob, thumbnail_blob, snapshot_blob, timelapse_blob, result completed|failed|canceled|stopped, duration_s, est_filament JSONB [{tray,type,color,grams,meters}], plate, slicer_meta JSONB, fetch_state pending|done|missing|error, fetch_error, created_by_user_id)`. `prints` + `print_job_id` FK unique nullable, `source` manual|auto, `quality` good|reject|null, `failure_reason`, `filament_cost`, `machine_cost`, `energy_kwh`, `energy_cost`, `total_cost`.
- **Services:** new `services/archive.py` (FTPS fetch → `pipeline/slicedmeta.py` → model resolution via `services/slicer_naming.py`); new `services/costing.py` (grams/1000 × `filament_cost_per_kg` + hours × `machine_cost_per_hour`; failed/canceled charged `est_g × layer/total_layers`). Retire client-side `web/src/lib/printCost.ts`.
- **Adapter:** `download_file(remote, dest)` + `list_dir(path)` on `BambuLanAdapter` via the vendored FTPS client.
- **printerd:** on terminal transition `send_task("archive_print", job_id)` (worker-io, 3 retries). `RUNNING` with no matching job → create an `external` PrintJob (Studio/touchscreen prints get archived too).
- **API:** `GET /api/archives?printer=&result=&model=`, `GET /api/archives/{id}`, `/{id}/thumbnail`, `PATCH /api/prints/{id}` (quality, failure_reason, actual grams), `POST /api/archives/{id}/reprint` (stub until P5). Register path params in `tests/test_auth_sweep.py`.
- **UI:** `components/printer/PrintJobHistory.tsx` → archive-backed table; new `components/print/RunDetailSheet.tsx` with Good/Reject.
- **Tests:** archive task vs fake FTPS fixture; external-job detection; partial/failed costing; migration up/down.
- **Live-verify (A1 Mini):** 3MF location after LAN vs Studio send (expect `/cache/<subtask>.3mf`); `/cache` purge behaviour; `gcode_state` on cancel.

## P3 — IA/shell overhaul + printer-first dashboard

- **API:** `GET /api/printers/overview` → per visible printer: Redis state, active job, next 3 queue items, last 5 runs.
- **Nav** (`components/shell/navItems.ts`): **Print** (gated) Dashboard `/`, Queue `/print/queue`, History `/print/history`, one dynamic entry per printer `/printers/$id` · **Library** Models `/library`, Collections, Add, Duplicates · **Inventory** Spools `/inventory/spools`, Materials `/inventory/materials`, Profiles & Recipes `/inventory/profiles` · **System** (pinned bottom) Background Jobs `/system/jobs`, Finance (P7, `billing:manage`), Settings (admin sections perm-gated).
- **Redirects:** `/`→`/library` when `printer_enabled=false` or user has no visible printer; `/dashboard`→History stats tab; `/printer`→`/printers/$id`; `/queue`→`/print/queue`; `/jobs`→`/system/jobs`. `ALL_NAV_ITEMS` single source; `SidebarPrinterWidget` stays compact.
- **Modify:** `routes.tsx`, `AppSidebar.tsx`, `CommandPalette.tsx`, `TopBar.tsx`, `SidebarPrinterWidget.tsx`, `PrinterStatusPanel.tsx`. **New:** `pages/PrintDashboardPage.tsx`, `components/print/PrinterCard.tsx`, `ActiveJobStrip.tsx`, `RecentRunsList.tsx`, `pages/PrintHistoryPage.tsx` (archive table + stats from `DashboardPage`), `pages/PrinterDetailPage.tsx`.
- **Dashboard:** top row `PrinterCard` grid (1–3 cols): camera tile 16:9 (placeholder until P4) w/ state badge, active job (thumb, name, plate, thin amber progress, layer N/M, mono ETA, pause/stop if `printers:control`), nozzle/bed spec rows, AMS slot chips, HA alert badge (P8). Right column: "Up next" queue (reorder + Print…), "Recent runs" (last 10: result dot, grams, cost, inline Good/Reject), review inbox card (P7). Empty state → `PrinterSetupCard`.
- **Visual density:** keep "workbench datasheet" (amber `oklch(0.646 0.176 41.1)`, Geist Mono). 13px table body, 28px rows, 12px uppercase tracked labels, `tabular-nums`, units muted, 1px borders/no shadows/6px radius. Amber only for primary action + in-progress; state tokens: printing amber, finished green, failed red, paused/held slate. Reuse `filament-chip`, `spec-row`; add `StatTile` + `Sparkline`. Invoke `frontend-design` skill when building. Offer the visual-companion mockup once at P3 kickoff.
- **Tests:** `navItems.test.ts` (groups + perm gating); redirects; dashboard at 0/1/2 printers; SSE updates card; update `DashboardPage`/`QueuePage` tests.

## P4 — Live camera + timelapse

- **Client:** new `printers/camera_a1.py`, own implementation: TLS socket to `host:6000`, 80-byte auth packet (`bblp` + access code), 16-byte header + JPEG frames; context-managed, socket timeout, interruptible.
- **printerd:** camera thread per printer only while viewers exist (Redis set `printer:{id}:camera:viewers`, 10 s TTL heartbeat). Latest frame → `printer:{id}:camera:frame` (5 s TTL) + pub/sub. ~5 fps cap; stop 30 s after last viewer; watchdog.
- **API (stateless, `printers:control` per printer):** `GET /api/printers/{id}/camera/stream` (multipart MJPEG from Redis), `/camera/snapshot` (503 + `Retry-After: 2` while warming). Signed-URL auth for `<img>`.
- **Adapter:** pass `timelapse` + `bed_levelling` through `upload_and_start` (raw `project_file` MQTT if vendored `start_print` lacks them). Archive task picks newest timelapse with mtime ≥ `started_at`; worker-cpu ffmpeg → MP4; one frame at `FINISH` → `snapshot_blob`.
- **UI:** `components/print/CameraTile.tsx` (lazy, pauses off-screen), `TimelapsePlayer.tsx`; in `PrinterCard`, `PrinterDetailPage`, `RunDetailSheet`.
- **Tests:** frame parser vs byte fixtures; viewer refcount + idle shutdown; 503 while warming; stop-before-start never hangs.
- **Live-verify:** auth packet/port 6000 on current firmware; fps; timelapse dir (`/timelapse/` vs `/timelapse/video/`) + format; SD-card requirement.

## P5 — Spool inventory, AMS mapping, one-modal Print flow

- **Schema:** `spools(material_id nullable, material_type, brand, color_hex, color_name, label_weight_g=1000, empty_weight_g, weight_used_g, cost_per_kg, location, low_stock_g, tray_uuid unique nullable, archived_at, last_used_at, created_by_user_id)`; `spool_slots(printer_id, ams_unit, slot [254=external], spool_id)` unique per printer/unit/slot; `spool_usage(spool_id, print_job_id, grams, source 3mf|ams_delta|manual)`.
- **Services:** `services/spool_inventory.py` (NOT `spool.py`). Remaining = label − used. Auto-assign by `tray_uuid` on AMS report. Usage on archive: per-tray 3MF grams, fallback AMS remain% delta; idempotent per job. Cost → spool `cost_per_kg`, fallback global. `services/ams_match.py`: exact type → nearest colour (ΔE OKLab) → override.
- **Adapter:** `PrinterPublicState.trays` + `tray_uuid`, `remain_pct`, `tray_type`, `ams_unit`.
- **API:** `/api/spools` CRUD (`inventory:write`); `PUT /api/printers/{id}/slots/{unit}/{slot}` (`printers:control`); `POST /api/print/plan` (file+plate+printer → suggested `ams_mapping`, estimates, warnings); `POST /api/print/send` (wraps `tasks/printing.py`, `printers:print`).
- **Filament tracking (the user's explicit ask; Spoolman is TABLED, no connector in this roadmap):** every archived run writes `spool_usage` rows per tray; spool detail shows a usage history (run, date, grams, source) and a remaining-weight sparkline; History stats gains per-material and per-spool grams/cost over time (reuses `services/stats.py` material usage, now fed from `spool_usage` instead of free-text `prints.filament`); low-stock spools surface as a muted badge on the dashboard AMS chips and an Inventory filter. Manual `prints` rows can still carry grams for prints the app never saw. Keep `spoolman_id` out of the schema until Spoolman is un-tabled.
- **UI:** `pages/SpoolsPage.tsx`, `components/inventory/SpoolTable.tsx`, `SpoolForm.tsx`, `SpoolUsageHistory.tsx`, `SlotAssignPopover.tsx` (from AMS chips on `PrinterCard`). Fold `MaterialsSection` into Inventory. New `components/print/PrintDialog.tsx`: left step rail, sticky footer (est time/grams/cost). Steps: 1 Source (skipped from model/reprint) · 2 Plate (thumb grid from slicedmeta) · 3 Profile ("As sliced" until P6) · 4 Filament (auto-mapped slots: swatch, type, remaining g; mismatch amber/red; override) · 5 Send (compatible idle printers the user can print on; bed-levelling/flow-cali/timelapse toggles; **Send now** / **Add to queue**). Launchers: ModelDetail, `RunDetailSheet` Reprint, command palette.
- **Tests:** matcher table; usage decrement + idempotency; AMS-delta fallback; RFID auto-assign; per-material/per-spool stats; low-stock threshold; PrintDialog step nav + override.
- **Live-verify:** AMS-lite `tray_uuid` Bambu vs third-party; `ams_mapping` index semantics; external-spool mapping value.

## P6 — Bambu Studio slicer sidecar, profiles, recipes, dispatch

- **Sidecar (user decision: Bambu Studio):** compose service `slicer` under profile `slicer` running the **Bambu Studio CLI**. Start from the prebuilt `ghcr.io/maziggy/bambu-studio-api` image (x86_64, HTTP + `/health`, port 3001) pinned by tag with a contract test; replace with our own thin FastAPI over the CLI (`bambu-studio --slice --export-3mf --load-settings --load-filaments --arrange --orient`) behind the same contract if the image's API is unstable or lags releases. Profiles import from Bambu Studio's `system/BBL` JSON bundle so A1 Mini presets match the desktop app. Setting `slicer_url`; env `TDMM_SLICER_URL`.
- **Schema:** `slicer_profiles(name, kind printer|process|filament, printer_model, nozzle_mm, data JSONB, source import|builtin, created_by_user_id)`; `print_recipes(name, printer/process profile FKs, filament_profile_ids JSONB, bed_type, options JSONB {auto_orient, arrange, bed_levelling, flow_cali, timelapse}, target_printer_id/target_printer_model nullable, fanout single|spread|first_n, n, created_by_user_id)`. `print_queue` + `printer_id`, `recipe_id`, `file_id`, `plate`, `copies`, `state` waiting|slicing|ready|dispatched|failed|held, `assigned_printer_id`, `print_job_id`, `attempts`, `last_error`.
- **Celery:** `slice_file` (worker-cpu; output = new revision file via slicedmeta). Beat `dispatch_queue` every 10 s: idle printers, compatibility (model, nozzle, loaded materials), hold flag, creator still `can_print`, fanout, enqueue send-print; retry up to N. Added to `celery_app._beat_schedule` additively.
- **API:** `/api/profiles`, `/api/recipes` CRUD; `POST /api/profiles/import`; `POST /api/queue/{id}/retry`.
- **UI:** `pages/ProfilesPage.tsx`; `QueuePage.tsx` → `/print/queue` with per-printer lanes + state chips; PrintDialog Profile step: "As sliced" or recipe → **Slice and send** / **Add to queue**.
- **Tests:** sidecar contract vs fake HTTP server; dispatcher fanout matrix; hold, compatibility rejection, retry, revoked-access stop.
- **Live-verify:** CLI output prints on the A1 Mini (plate index, `.gcode.3mf` md5); bed-type naming; flag set for pinned version.

## P7 — Job review + cost centers/budgets/ledger

- **Schema:** `cost_centers(name, owner_user_id | group_id (one set), budget_total, budget_monthly, balance cache, active)`; `ledger_entries(cost_center_id, kind charge|deposit|adjustment|reservation|release, amount numeric(12,2) signed, print_queue_id, print_id, created_by_user_id, note, created_at)` append-only with idempotency key `(print_queue_id, kind)`; `print_queue` + `cost_center_id`, `review_state` none|pending|approved|rejected, `reviewed_by`, `reviewed_at`, `review_note`, `reserved_cost`, `hold_reason`. App setting `billing_enabled` default off → every billing hook no-ops.
- **Flow:** enqueue → `pending` if `printer_access.requires_review` and user lacks `queue:review`, else `none`. Reserve (billing on): estimate = grams × spool price + machine rate × time; lock cost-center row, check balance − open reservations + monthly limit; fits → `reservation`, else `hold_reason=over_budget`. `POST /queue/{id}/review {approve|reject, note}` (`queue:review` + printer access; reject releases). Dispatcher filters `review_state IN (none, approved) AND hold_reason IS NULL`. Archive → `release` then `charge` actual `prints.total_cost` (failed/cancelled pro-rata). Deposits/adjustments need `billing:manage`. Every user gets a private cost center; group centers optional, picked at queue time. Nightly job recomputes balances from the ledger.
- **API/UI:** `/finance/cost-centers`, `/finance/ledger?cost_center=`, `/finance/deposits`; `GET /queue/review-inbox` + count → dashboard card + nav badge; Finance page (`billing:manage`; members see own centers); queue chips Pending review / Rejected / Over budget.
- **Tests:** review state machine (+409 invalid); reserve/release/charge incl. failed partial, rejected, concurrent enqueues racing a budget (row lock); monthly rollover; billing-off → zero ledger rows; reviewers limited to their printers.

## P8 — Home Assistant both ways, energy, hold

- **Push:** printerd `ha_bridge` thread (paho-mqtt). Settings `ha_mqtt_url`, user/password (encrypted via `services/secrets_at_rest.py`), `ha_discovery_prefix`=`homeassistant`. Retained discovery per printer device: sensors (state, progress, remaining, layer, nozzle/bed temp, current job, last result, queue length), binary_sensor printing, camera (snapshot every 30 s while printing), buttons pause/resume/stop → `printer:{id}:commands`. LWT availability; ≤1 publish/s per printer.
- **Pull:** settings `ha_url`, `ha_token` (encrypted). Table `ha_links(printer_id nullable, entity_id, role energy_kwh|power_w|door|smoke|temp|humidity, alert_condition JSONB, hold_queue bool, pause_print bool default off)`. Beat poll HA REST every 15 s → Redis `ha:alert:{printer}`; dispatcher skips held. Energy: kWh at job start/finish → `prints.energy_kwh`; `energy_cost_per_kwh` in costing (feeds P7 charges).
- **UI:** `components/settings/HomeAssistantCard.tsx` (`settings:write`; test both directions, entity link table); alert badge on `PrinterCard`; "Held" chip in queue.
- **Tests:** discovery payload snapshots; command routing; alert eval + hold; energy delta across counter reset; reconnect after broker restart (testcontainers mosquitto).
- **Live-verify:** entities in the user's HA; HA pause pauses the A1 Mini; door toggle → Held; smart-plug kWh delta on a run.

## Top risks

1. **Permission gaps in later phases.** A new mutating route without `require_perm`/printer ACL is open to every member. Mitigation: the P1 viewer-403 sweep fails CI for unlisted routes.
2. **OIDC account takeover.** Link only on `email_verified`; `(issuer, sub)` unique; one-shot Redis state; `link_by_email` explicit.
3. **A1 Mini protocol gaps** (camera handshake, cache/timelapse paths, `ams_mapping`). Live-verify spike opens each printer phase; adapter degrades to `fetch_state=missing`.
4. **Camera thread wedging printerd.** Own socket client + timeouts + watchdog + stop-before-start regression test.
5. **Ledger drift** on dispatcher crash / retried archive. Idempotency key per `(print_queue_id, kind)` + nightly recompute.
6. **Sidecar drift.** Bambu Studio CLI flags change between releases; pinned tag + contract test + `/health` version check.
7. **IA move breaks links/tests.** Redirects + test updates in the same PR.

## Verification (every phase)

```
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . \
  && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
cd web && npm run build && npx oxlint && npx vitest run
docker compose up -d --build api worker-io worker-cpu beat printerd
```
(pytest needs the sandbox disabled for testcontainers; `tsc --noEmit` is a no-op here, use `npm run build`.)

Manual drills: **P1** Authentik login as mapped-group user → admin; viewer account gets 403 on writes; extension token still imports. **P2** send a small print, confirm archive row w/ 3MF/thumb/grams/cost; Studio-started print cancelled at ~20% → `canceled` run w/ partial cost. **P3** `/` shows live card, old URLs redirect, SSE ticks, viewer sees no admin nav. **P4** two tabs → one upstream (~5 fps), thread stops 30 s after close, snapshot 503→JPEG, timelapse attaches. **P5** RFID spool auto-assigns; two-colour print via modal; `weight_used` drops. **P6** slice an STL via recipe and send; 3 copies `first_n=1` run in order; force fail + retry. **P7** member on a `requires_review` printer → Pending; approve → prints; over-budget → held; ledger shows reserve/release/charge. **P8** entities in HA, HA pause works, door toggle holds queue, kWh delta recorded.
