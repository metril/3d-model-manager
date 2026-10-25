# Bambuddy parity v3 — roadmap T1–T8

## Context
v2 (Q1–Q11, PRs #14–#36) delivered the command bus, capability table, bambuddy-layout card, camera v2, HMS/firmware, notifications, plugs, stats, Spoolman, Obico, virtual printer. The 2026-10-24 audit (memory `bambuddy-parity-v3-gaps`) found the remaining real gaps against upstream bambuddy (scratchpad clone `upstream/bambuddy`, commit 98ecaf5 — re-clone shallow if missing):

- **Printer layer**: no AMS drying (`caps.ams_drying` flag + `FilamentsSection.onDry` button exist but nothing supplies a handler), no K-profiles, no xcam/print options, no device-side AMS slot configure (`POST /printers/{id}/slots/{unit}/{slot}` is DB-only), no plate-clear gate, no SD browser routes (adapter FTPS helpers `bambu.py` ~865–882 unused).
- **Queue**: no scheduling, manual release, previous-success chaining, preheat/soak, auto power-off, batches.
- **Inventory**: no suppliers, structured locations, colour catalog, bulk ops, CSV, labels.
- **Auth**: no 2FA, no global SMTP.
- **Archive**: no edit/delete, compare, purge, calendar, outcome confirm, timelapse editor, photos.
- **Misc**: no Apprise/Slack, no per-user email prefs, no G-code toolpath viewer.

## User decisions (2026-10-24)
- Scope: **all of T1–T8**, done thoroughly, nothing parked. T8b (G-code viewer) is a separable sub-phase.
- **Apprise** is the new notification provider (user: "support apprise and that would give us a lot of providers for free"). Slack is covered by Apprise (`slack://`); no bespoke Slack kind.
- **LDAP dropped** (user, 2026-10-24): OIDC via Authentik/Authelia/Keycloak/Entra already covers directory-backed logins. T6 = 2FA + SMTP mailer only.
- User is bambuddy's author: port upstream code, payloads, data tables, vendored files freely (no attribution/NOTICE). Prefer porting over reinventing, adapted to Postgres/Celery/printerd + our permission model.
- Locales: `en` only (other locales OUT of scope). All new strings via `t()` so `scripts/i18n-check.mjs` stays green.
- Autonomous execution per memory `autonomous-roadmap-execution`: plan → implement → one Opus review → PR → green CI → merge → next, no continue prompts.

## Cross-cutting rules
- **Command envelope v2 for every printer command**: `DISPATCH[name]=Spec(...)` in `printers/commands.py`; adapter method on `printers/base.py` (default `NotImplementedError`) + `bambu.py` impl via `_publish_raw` with the upstream payload; `fake.py` stub recording `.calls`; `_ROUTES` entry in `api/printer_control.py` or a dedicated route through `run_command`. State-changing commands → `_AUDITED`.
- **Capabilities** (`printers/capabilities.py`): fill `ams_drying` from upstream unsupported/screen-only/min-firmware tables; add `drying_screen_only`, `ams_drying_while_printing`, `kprofiles`, `xcam_modules[]`, `sd_browser`, `sd_print`. Web hides by capability; API 422 `unsupported` (409 for screen-only drying); `verified=False` → "untested" tooltip.
- **One PR per phase/half-phase**, own alembic revision chained from head `c9d4e6f8b2a1` (parallel branches re-chain `down_revision` on rebase), `backend/tests/test_migrations_t{n}.py` (up→down→up + backfill assertions).
- **Tables**: every new table has `created_by_user_id` (SET NULL) + `created_at`. AuditLog rows inline for config/destructive actions.
- **Permissions**: no new perms. `printers:control` (printer ops), `inventory:write` (T5), `queue:review` (manual start, batches), `settings:write` (settings groups), `admin` (2FA reset, SMTP, purge run). Self-service routes (2FA, email prefs) are session-only → `tests/test_perm_sweep.py VIEWER_ALLOWLIST`. Public routes → `tests/test_auth_sweep.py PUBLIC_ROUTES`; new path params → `_PATH_PARAM_VALUES`.
- **Settings**: per-feature typed groups in the existing `Setting` store via `GET/PUT /settings/{group}` (`settings:write`); server-level (SMTP) in `services/server_config.py ServerConfig` + `ENV_SEED`.
- **Web**: dialogs `sm:max-w-*`; modals via `PrinterModalKind` union (`modals/usePrinterModals.tsx`); control calls via `useControl`/`ControlPath` (`api/printerControl.ts`); types appended per phase in `api/types.ts`; i18n namespaces per feature in `en.json` (`drying.*`, `kprofile.*`, `printerOps.*`, `queueSched.*`, `spoolAdmin.*`, `twofa.*`, `smtp.*`, `archiveX.*`, `apprise.*`, `gcodeViewer.*`).
- **Gates**: backend `uv run pytest` (testcontainers, sandbox OFF); web `npm run build` (tsc -b) + oxlint + `npm test`. All green before review.
- **Process**: ≤5 Sonnet implementers in parallel, each in its own worktree on branch `t{n}[-be|-web]-<slug>`, rebased on main before PR. Controller writes the API contract (paths/schemas/perms) into each phase plan before splitting be/web; web agent mocks the contract until be merges. One Opus whole-branch review per PR. Serialize web PRs touching `api/types.ts`/`en.json`/the same hot file. Agents never stash/checkout in a shared tree (memory `agents-no-git-stash`).
- **Spec commit**: first commit of T3-be adds this file's spec content as `docs/superpowers/specs/2026-10-24-bambuddy-parity-v3-design.md`.

## T1 AMS drying
**Backend**
- `services/drying.py`: port upstream `send_drying_command` + `drying_preflight.py` (`check_drying_supported`, `find_ams_unit`, `blocking_reason_codes`, `DRY_SF_REASON_MESSAGES`, waiting-reason tokens).
- Capabilities: copy `_DRYING_UNSUPPORTED_MODELS` (A1, A1MINI, …), `_DRYING_SCREEN_ONLY_MODELS` (P1P, P1S), `_DRYING_MIN_FIRMWARE`, `_DRY_WHILE_PRINTING_MIN_FIRMWARE`; `capabilities_for` considers firmware.
- Commands: `drying_start(ams_id, temp, duration_min, filament, rotate_tray)` → `{"print":{"command":"ams_filament_drying","ams_id","temp","cooling_temp":20,"duration","humidity":0,"mode":1,"rotate_tray","filament","close_power_conflict":false}}`; `drying_stop(ams_id)` = same with `mode:0, cooling_temp:0`. `ams_id` ∈ 0–3 or 128–135. Preflight in Spec validate/cold_check: screen-only → 409 `screen_only`; unsupported → 422; non-empty `dry_sf_reason` → 409 with codes; empty `filament` backfilled from loaded tray `tray_type`. `refuse_printing` unless `ams_drying_while_printing`.
- State: `_ams_units` (bambu.py:367) adds `dry_time_min`, `dry_status` (`(info>>4)&0xF`: 0 off,1 checking,2 drying,3 cooling,4 stopping,5 error), `dry_sub_status` (`(info>>22)&0xF`), `dry_sf_reason[]`, `humidity_index` (raw 1–5), cached `dry_target_temp/dry_filament` (adapter-side, printer never echoes; cleared when status→0). Humidity stays `humidity_raw` %, fallback ported `ams_humidity_percent(index)`. Extend `PrinterPublicState.ams_units` + status schema.
- Settings group `drying`: `presets{filament:{n3f_temp,n3f_hours,n3s_temp,n3s_hours}}` seeded from upstream table (PLA 45/45/12/12, PETG 65/65/12/12, TPU 65/75/12/18, ABS/ASA 65/80/12/8, PA 65/85/12/12, PC 65/80/12/8, PVA 65/85/12/18); `ams_humidity_good/fair` (40/60); `per_type_thresholds`; `ambient_drying_enabled` + `ambient_sustained_min`; `queue_drying_enabled/block`; `print_drying_enabled`.
- Table `scheduled_dryings(id, printer_id, ams_id, temp, duration_hours, filament, rotate_tray, start_after, status pending|waiting|running|done|failed|cancelled, waiting_reason, error_code, started_at, finished_at, source manual|ambient|queue, created_by_user_id, created_at)`.
- Routes: `POST /printers/{id}/drying/start|stop` (control, audited); `GET/POST /printers/{id}/drying/schedules`; `DELETE /drying/schedules/{sid}`; `GET /drying/presets`.
- Celery beat `drying.tick` (60 s, additive in `celery_app.py`): (1) start due schedules via preflight, record waiting reason, `did_not_start` if `dry_status` unchanged 90 s after send; (2) ambient auto-dry on sustained humidity + idle; (3) queue drying before dispatch, `hold_reason="drying"` when `block`; (4) new `SmartPlug.auto_off_after_drying` → `smart_plugs/manager.command(...,"off")` when drying ends + idle.
- Notify events `drying_started/finished/failed` in `catalog.py _EVENTS`.

**Web**: `modals/DryingDialog.tsx` (kind `drying`, `sm:max-w-lg`: filament picker, presets n3f/n3s, temp/hours clamped, rotate-tray, start now | schedule at, blocked-reason panel). `PrinterCard` passes `onDry` to `FilamentsSection`. Unit header drying chip (status, countdown, target, Stop). Screen-only → state + tooltip. Settings → Printer: `DryingSettingsCard` + scheduled list.

**Tests**: `test_drying.py` (payload goldens, preflight matrix, bit parsing from recorded H2D/X1 AMS fixture in `printer_fixtures.py`, tick states, plug auto-off); `test_capabilities.py`; `DryingDialog.test.tsx`, `FilamentsSection.test.tsx`.

**Live (A1 Mini)**: AMS-lite is unsupported upstream → verify button hidden, API 422, humidity % correct. Drying payload fixture-only; drying-capable models stay `verified=False`.

## T2 K-profiles (pressure advance)
**Backend**
- Commands: `kprofile_list(nozzle_diameter="0.4")` → `extrusion_cali_get` (`filament_id:""`); parse `filaments[]{cali_idx,setting_id,filament_id,name,k_value,n_coef,extruder_id?,nozzle_id?,ams_id,tray_id}`, per-item `nozzle_diameter` falls back to envelope; reply via cmd_result (≤3 s else 202 + web polls `GET …/kprofiles?request_id=`). `kprofile_set` → `extrusion_cali_set` (`tray_id:0` always; new `setting_id="PF"+17 digits`). `kprofile_delete` X1 variant (`setting_id`) or H2D (`extruder_id`+`nozzle_id`) by `capabilities.dual_nozzle`. `kprofile_select(ams_id, tray_id, cali_idx, filament_id, nozzle_diameter)` → `extrusion_cali_sel`. All refuse while printing except list.
- Tables: `spool_k_profiles(id, spool_id FK cascade, printer_id FK cascade, extruder, nozzle_diameter, nozzle_type, k_value, name, cali_idx, setting_id, uq(spool_id,printer_id,extruder,nozzle_diameter), …)`; `kprofile_notes(printer_id, setting_id, note, uq(printer_id,setting_id), …)`.
- `services/kprofiles.py reapply_lost_kprofiles(printer_id, state)`: tray with assigned spool + `cali_idx == -1` + matching stored profile → re-send `extrusion_cali_sel`; never override a different real profile. Called from printerd on idle push (debounced 10 min/tray) and inside `_execute` before `start_print` (keeps `dispatcher.py` untouched).
- Slot configure (T3) chains `extrusion_cali_sel` when the spool has a profile.
- Routes: `GET /printers/{id}/kprofiles?nozzle=`, `POST …/kprofiles`, `DELETE …/kprofiles/{setting_id}`, `PUT …/kprofiles/{setting_id}/note` (control); `GET/PUT/DELETE /spools/{id}/kprofiles` (`inventory:write`).

**Web**: `KProfilesModal` (kind `kprofiles`, `sm:max-w-3xl`: nozzle tabs, extruder tabs on dual-nozzle, table name/K/n/filament/note, inline edit, add, delete w/ ConfirmDialog); opened from card menu + `SlotCircle` dropdown "Pressure advance…" (links tray's spool to profile). `SpoolForm` "K-profiles" section.

**Tests**: `test_kprofiles.py` (both delete variants, PF format, parsing ± per-item nozzle, drift fixer matrix); `test_printerd.py` reapply before start; `KProfilesModal.test.tsx`.

**Live (A1 Mini)**: list @0.4; create/rename/delete; link to spool; power-cycle → `cali_idx` restored on first idle push. H2D variant fixture-only (`verified=False`).

## T3 Printer ops
**Backend**
- **Clear plate**: column `printers.awaiting_plate_clear bool default false`; printerd `_transition_active_job` sets it on FINISH/FAILED (external jobs too); `POST /printers/{id}/clear-plate` (control, audited) clears, 400 if not awaiting and state not FINISH/FAILED; dispatcher `eligible_queue_items` filter only when setting `queue.require_plate_clear` on (printer ineligible, reason `awaiting_plate_clear` in overview); internal hook `plate_cleared(printer_id, user)` for T7.
- **Print options**: `set_xcam(module, enable, sensitivity low|medium|high|never_halt)` → `{"xcam":{"command":"xcam_control_set","module_name","control":true,"enable","print_halt":true,"halt_print_sensitivity"}}` (omit sensitivity for never_halt); modules spaghetti_detector, first_layer_inspector, printing_monitor, buildplate_marker_detector, allow_skip_parts, pileup_detector, clump_detector, airprint_detector. `set_print_option(auto_recovery|auto_switch_filament, bool)` → `{"print":{"command":"print_option",…}}`. 3 s echo-hold on adapter. Parse `xcam` (`cfg` bitmask + fields) + print_option flags → `PrinterPublicState.print_options`; capability `xcam_modules` = modules the printer reports.
- **AMS slot configure**: `ams_slot_configure(ams_id, tray_id, tray_info_idx, tray_type, sub_brand, color RRGGBBAA, nozzle_min, nozzle_max)` → `ams_filament_setting` with `slot_id`; reset variant (empties, `00000000`, temps 0). Existing `POST /printers/{id}/slots/{unit}/{slot}` gains `?apply=device` (default true when online): DB write + command, chains `extrusion_cali_sel` behind a capability/hasattr check so T3 stays independent of T2.
- **AMS labels**: table `ams_labels(ams_serial, ams_id, label, uq(ams_serial), …)`; `PUT /printers/{id}/ams/{ams_id}/label`.
- **SD browser**: adapter `delete_file(path)`; routes (control) `GET /printers/{id}/files?path=`, `GET …/files/download?path=` (stream), `DELETE …/files?path=`, `GET …/files/storage` (FTPS `AVBL`/`STAT`, fallback `sdcard` from state). Paths normalised, confined to `/`, no `..`. `POST …/files/print {path, plate?, use_ams}` reuses `start_print` with `url:"file:///sdcard/<path>"` for `.3mf`/`.gcode.3mf`, gated `sd_print`, creates EXTERNAL job.

**Web**: plate-clear pill + "Mark plate cleared" in `StatusSection`; `PrintOptionsModal` (kind `printOptions`: module toggle + sensitivity rows, Auto-recovery, "AMS backup"=auto_switch_filament); `SlotCircle` menu "Configure slot…" (`SlotConfigDialog`: material, sub-brand, colour via `normalizeHex`, temps, Reset) + "Rename AMS"; `FileManagerModal` (`sm:max-w-4xl`: breadcrumb, list, download, delete, storage bar, Print when `sd_print`, preview slot for T8b).

**Tests**: `test_printer_ops.py` (flag lifecycle + 400, gate on/off, xcam goldens incl. never_halt, echo-hold, slot goldens, label CRUD, traversal rejection, fake FTPS list/delete, SD print payload); vitest per modal.

**Live (A1 Mini)**: finish → pill → clear → queue dispatches; toggle reported xcam modules; auto-recovery; configure slot (Studio reflects); browse/download/delete SD; print SD 3MF.

## T4 Queue scheduling + batches
**Backend**
- `print_queue_entries` += `scheduled_at timestamptz`, `manual_start`, `require_previous_success`, `auto_off_after`, `preheat_override null|on|off`, `preheat_chamber_target`, `batch_id FK SET NULL`, `released_at`, `released_by_user_id`. `QueueState.SKIPPED` added.
- `dispatcher.py`: `eligible_queue_items` (:132) excludes `scheduled_at > now()` and `manual_start and released_at is null`. `_reconcile` (:162): previous = latest terminal entry on same printer (same batch if set); FAILED, or DONE with archive result failed/aborted → current → SKIPPED + `hold_reason="previous_failed"` + event `queue_item_skipped`; cancelled neutral (look further back); none → run.
- **Preheat/soak** stage before `_send_one`, beat-driven (never blocks worker): chamber-heater printers `M141 S<t>`, others bed soak (`M140` filament bed temp); wait within 2 °C or `max_wait_s` 60–3600; soak `soak_s` 0–1800; sub-status `preheating|soaking` in `print_options.stage`. Settings group `queue_preheat{enabled, chamber_targets{filament:°C}, max_wait_s, soak_s, keep_warm_enabled, keep_warm_min}`.
- **Auto power-off**: on DONE with `auto_off_after` → `smart_plugs/manager.command(plug,"off")` via `_maybe_auto_off` path; respects plug strategy, never while more queued work targets that printer.
- **Manual release**: `POST /queue/{id}/start` (`queue:review`) sets `released_at`; `POST /queue/{id}/cancel-schedule`; `POST /printers/{id}/queue/resume` acknowledges previous-failure holds + re-queues SKIPPED.
- **Batches**: table `print_batches(id, name, model_id?, file_ref JSONB, quantity, status draft|active|done|cancelled, project_id?, due_date, notes, plate_targets JSONB, …)`; routes `GET/POST /batches`, `PATCH/DELETE /batches/{id}`, `POST /batches/{id}/dispatch|ungroup` (`queue:review`). Dispatch fans out `quantity` entries (`copies=1`) through existing Fanout/enqueue, distributing plate targets; status derived from entries.

**Web**: `QueueEntry` fields; `PrintDialog` collapsible "Scheduling" step (start at, manual start, require prev success, power off after, preheat override + target, batch picker/new); `QueueRow` chips (scheduled, manual + Start, skipped + reason + Resume, preheating/soaking, power-off); `BatchesPanel` tab on QueuePage; Settings → Printer `QueuePreheatCard`.

**Tests**: `test_queue_schedule.py` (exclusion, release, previous-success matrix, preheat state machine w/ fake temps, auto-off, batch fanout/ungroup/status); enum migration test; `QueuePage.test.tsx`, `PrintDialog.test.tsx`.

**Live (A1 Mini)**: schedule +5 min; manual start; fail #1 → #2 skipped + notification; bed soak before print (no chamber → `M141` fixture-only); plug off after print.

## T5 Spool housekeeping
**Backend**
- Tables: `suppliers(name, name_key uq, website, customer_number, note)`; `spool_suppliers(spool_id, supplier_id, article_number, quoted_price_per_kg, is_purchase_source, uq)`; `storage_locations(name uq, name_key uq, identifier)`; `location_ha_sensors(location_id, name, entity_id, kind, device_class, unit, alert_above, alert_below, notify_on_alert, show_on_card, last_state, last_changed_at, alerting)`; `color_catalog(manufacturer, color_name, hex, material, is_default, extra_colors JSONB, effect_type)` seeded from upstream data. `spools.location_id FK SET NULL` replaces free-text `location` (ruling 5).
- HA sensors polled by existing HA bridge (`services/ha_config.py`/client) in a beat tick; crossings emit `location_sensor_alert`.
- Routes: CRUD `/suppliers`, `/storage-locations` (+`/sensors`), `/color-catalog` (+`/lookup`, `/search`, `/by-material/{m}`); `POST /spools/bulk-update {ids, update}` → per-id `{id, ok, error?}`; `bulk-delete|bulk-archive|bulk-restore`; `GET /spools/export.csv`; `POST /spools/import?dry_run=true` (multipart → per-row create/update/error) then `POST /spools/import/commit {token}` (Redis 15 min). Columns per upstream `spool_csv.py`.
- Labels: `services/spool_labels.py` (`reportlab` + `qrcode[pil]`, PNG via `pypdfium2` — new deps); templates `tight|roomy`, A4/Letter/roll; QR = spool URL; `POST /spools/labels {ids, template, format pdf|png}`, `POST /spools/labels/preview`.

**Web**: `SpoolTable` checkbox column + sticky toolbar (Bulk edit, Archive, Delete, Export CSV, Labels); `BulkEditDialog`, `CsvImportDialog` (upload → diff table → commit), `LabelsDialog` (preview); `SpoolForm` location combobox (inline create), suppliers sub-list, colour-catalog autocomplete; "Materials & storage" tabs strip on `SpoolsPage` (Suppliers, Locations+sensors, Colour catalog) — avoids `SettingsPage.tsx`.

**Tests**: `test_spool_housekeeping.py` (location backfill dedupe, bulk per-id errors, CSV round-trip + dry-run no-write, label bytes + QR decode, HA alert); vitest toolbar/CSV/SpoolForm.

**Live**: export/import round-trip; label sheet; HA humidity sensor on a location.

## T6 Auth: 2FA + mailer (LDAP dropped)
**Backend**
- **SMTP**: `ServerConfig.smtp{host, port, security none|starttls|tls, username, password_enc, from_addr, from_name}` + `ENV_SEED`; `services/mailer.py send(to, subject, text, html?)` via Celery `mail.send` w/ retries; `POST /server-settings/smtp/test` (admin). Email notify provider gains "use server SMTP".
- Tables: `user_totp(user_id uq, secret_enc, is_enabled, backup_codes_json (argon2), last_totp_counter, enabled_at)`; `user_otp_codes(user_id, code_hash, attempts, used, expires_at)`; `auth_ephemeral_tokens(token_hash uq, token_type pre_auth|totp_setup|oidc_2fa, user_id, username, payload JSONB, attempts, expires_at)` swept by beat; `users.email_otp_enabled`.
- TOTP `pyotp` `valid_window=1`, replay guard (counter ≤ last rejected). Email OTP 6 digits/10 min/5 attempts/3 sends per 10 min. Backup codes 10×8 single-use.
- Login (`api/auth.py:50`): after credential check, if 2FA → `{requires_2fa:true, pre_auth_token, methods[]}` without `start_session`; `POST /auth/2fa/verify {pre_auth_token, method, code}` → `start_session`; `POST /auth/2fa/email/send`. Self-service: `GET /auth/2fa/status`, `POST /auth/2fa/totp/setup|enable|disable|regenerate-backup`, `POST /auth/2fa/email/enable|confirm|disable`. Admin `POST /users/{id}/2fa/reset`; setting `security.require_2fa` (ruling 6). OIDC callback respects 2FA.
- Sweeps: `/auth/2fa/verify`, `/auth/2fa/email/send` → `PUBLIC_ROUTES`; self-service 2FA → `VIEWER_ALLOWLIST`.

**Web**: `LoginPage` response union → code step w/ method tabs + "send email code"; `TwoFactorCard` beside `ChangePasswordCard` (QR, enable, backup codes once + download, email OTP); `Me` += `two_factor_enabled, must_enroll_2fa` (blocking enrolment screen when required); Access tab "Require 2FA" toggle + per-provider `trusts_idp_mfa` switch on `OidcProvidersCard`; Server tab `SmtpCard` + test-send.

**Tests**: `test_2fa.py` (window, replay, backup single-use, rate limit, pre-auth expiry/attempts, OIDC path, require-2FA); `test_mailer.py` (aiosmtpd); sweeps; `LoginPage.test.tsx`, `TwoFactorCard.test.tsx`.

**Live**: TOTP w/ phone app; log out/in; email OTP via real SMTP; OIDC login with 2FA enabled shows the code step.

## T7 Archive extras
**Backend**
- `PATCH /archives/{id}` (notes, result override, tags; `archives:write`-equivalent = existing print perm or owner) + `DELETE /archives/{id}` (admin; archive row + blob refs, print_job stays).
- `GET /archives/compare?ids=` (2–5, else 422): slicer_meta diff, filament, est vs actual time, success correlation per differing setting value.
- Purge: settings group `archive_purge{enabled, older_than_days 1–3650, keep_failed, keep_with_verdict}`; `POST /archives/purge/preview` (count, bytes), `/purge/run` (admin); nightly beat; `purge_stats` in settings.
- Calendar: `GET /archives/calendar?from&to` → per-day `{date, count, ok, failed, hours}`.
- **Outcome confirm**: archive cols `confirm_requested, confirm_token uq, confirm_token_used_at, user_verdict good|bad, user_verdict_source dialog|link|plate_clear|card|api, verdict_at, verdict_by_user_id`. On finish, printerd post-print hook sets `confirm_requested` when `confirm.enabled` (and `confirm_external_prints` for external), mints token, emits `print_confirm_request` with good/bad links. Public `GET /archives/confirm/{token}/{verdict}` renders prompt page only; `POST` records; `?tap=1` auto-submits. `POST /archives/{id}/verdict` for dialog/card/api. Settings `default_confirm_outcome` (applied after 24 h by beat, source `api`), `confirm_default_good_on_plate_clear` (T3 hook). Verdict maps to `result` only when different (bad → failed).
- **Timelapse editor**: `GET /archives/{id}/timelapse/info` (ffprobe), `GET …/timelapse/thumbnails?n=` (cached strip), `POST …/timelapse/process` (form `trim_start, trim_end, speed 0.25–4, save_mode replace|new`) → Celery `media.timelapse_edit` (reuse `tasks/media.py` helpers) → 202 + task id; `new` stored in `photos` with `kind:"timelapse"`.
- **Photos**: column `photos JSONB []` (`{filename, blob, kind, uploaded_by, at}`); `POST/GET /archives/{id}/photos`, `GET/DELETE …/photos/{filename}` (jpeg/png/webp ≤15 MB, re-encoded, EXIF stripped).

**Web**: `PrintHistoryPage` third tab `calendar` (month grid, success colour, click → filtered runs); `RunDetailSheet` edit/delete, verdict card, photo gallery + upload, "Edit timelapse" → `TimelapseEditor` (extends `TimelapsePlayer`: trim slider, speed, thumbnails, save mode); `RecentRunsList` multi-select → `ArchiveCompareDialog` (`sm:max-w-5xl`); `PostPrintConfirmDialog` for the job owner when online; Settings → Storage `ArchivePurgeCard`.

**Tests**: `test_archive_extras.py` (PATCH/DELETE perms, compare bounds, purge preview==run, purge leaves print_jobs + ledger untouched, confirm GET idempotent/POST single-use, plate-clear tie-in, default sweep, photo validation, timelapse process on tiny ffmpeg fixture); auth-sweep public route; vitest calendar/editor/compare.

**Live (A1 Mini)**: finish → ntfy confirm link → tap good → verdict shown; trim timelapse 2×; attach photo.

## T8 Smaller items
**T8a Apprise + per-user email prefs**
- New notify provider `apprise` (`services/notify/apprise.py`). **Integration = the `apprise` Python library in-process**, not the Apprise API server: add `apprise` (pin current 1.9.x) to backend deps; the provider's `send` runs inside the existing synchronous Celery `send_one` task like every other provider. Fields: `urls` (multi-line textarea, `secret=True` → encrypted at rest; one Apprise URL per line, e.g. `slack://…`, `discord://…`, `gotify://…`, `mailto://…`, or `apprise://host/key` for anyone already running the Apprise API container — no special handling needed), optional `tag`. `send`: `a = apprise.Apprise()`; add each URL individually (invalid URL → `ProviderError` naming the line number, URL redacted); photo → write bytes to a `NamedTemporaryFile` and pass `attach=apprise.AppriseAttachment(path)` when `supports_photo`; call `a.notify(title=…, body=…, body_format=TEXT)` per URL (not batched) so per-target failures are reported individually in `NotificationLog`; any failure → `ProviderError` listing failed targets by scheme+host only. Catalog label "Apprise (Slack, Discord, Matrix, Gotify, 100+ services)"; description links the Apprise URL wiki. Test route via existing `/notifications/test`. `kindIcon` → generic bell. Apprise's transitive deps (requests, requests-oauthlib, PyYAML, markdown) are acceptable; no network at import. No bespoke Slack kind (ruling 12).
- Table `user_email_preferences(user_id uq, notify_print_start, notify_print_complete, notify_print_failed, notify_print_stopped, …)`; `GET/PUT /user-notifications/preferences` (session; `VIEWER_ALLOWLIST`). `events.emit` fans out to the job owner via T6 mailer when user has email + matching pref.
- Web: `EmailPrefsCard` in General tab (shown only when SMTP configured).
- Tests: `test_notify_apprise.py` (apprise mocked; URL parsing, attach path, failure → ProviderError), `test_email_prefs.py`.
- Live: `slack://` incoming-webhook URL via Apprise receives a finish message with photo; finish email arrives.

**T8b G-code toolpath viewer (separable)**
- Backend: `GET /archives/{id}/gcode?plate=` streams `Metadata/plate_N.gcode` from archived 3MF (`printers/gcode3mf.py`), gzip, 404 when absent; `GET /printers/{id}/files/gcode?path=` from an SD 3MF (control). New `api/archive_gcode.py`.
- Web: vendor upstream `frontend/src/lib/vendor/toolpathRenderer.js` + `.d.ts` verbatim into `web/src/lib/vendor/` (17 KB, takes THREE namespace; we ship `three`); port `gcodeToolpath.ts` + `GcodeToolpathViewer.tsx` (layer slider, feature/range colouring), lazy-loaded; mount in `RunDetailSheet` "Toolpath" tab + `FileManagerModal` preview slot.
- Tests: `gcodeToolpath.test.ts`, `test_archive_gcode.py`. Live: A1 Mini archive renders layers.

## Dependencies
- T3-be → T1-be → T2-be (printer hot files, serialized). T3-be → T4-be (plate gate lands first; T4 then owns `dispatcher.py`). T3-be → T7-be (`plate_cleared` hook). T6-be → T8a (mailer). T7-be → T8b-be (rebase after). T3-web → T1-web → T2-web (card/modal hot files). T5 and T6 independent.

## Execution: 5 slots, always full

**Five Sonnet implementers run in parallel at all times.** There are no wave boundaries: the moment one PR merges, the controller starts the next item whose dependencies are merged. Items are ordered below; the controller takes the first ready one. Each item is its own worktree + branch, own alembic revision (re-chained on rebase), and one Opus whole-branch review before merge.

**Ready order (16 items)**

| # | Item | Starts after | Owns (hot files nobody else touches while it runs) |
|---|---|---|---|
| 1 | T3-be | — | `commands.py`, `bambu.py`, `base.py`, `fake.py`, `capabilities.py`, `printer_control.py`, `printerd.py`, `dispatcher.py` (gate only) |
| 2 | T5-be | — | `models/inventory.py`, `api/spools.py`, `services/spool_inventory.py` |
| 3 | T6-be | — | `server_config.py`, `api/auth.py`, `deps.py`, `models/auth.py`, `services/oidc.py` |
| 4 | T5-web | — (mocks T5-be contract) | `SpoolTable`, `SpoolForm`, `SpoolsPage`, `api/spools.ts` |
| 5 | T8b-web | — (mocks gcode endpoint) | `web/src/lib/vendor/*`, `GcodeToolpathViewer`, `gcodeToolpath.ts` |
| 6 | T1-be | T3-be | printer hot files, `celery_app.py` (drying tick), `smart_plugs/manager.py` |
| 7 | T4-be | T3-be | `dispatcher.py`, `services/queue.py`, `models/library.py`, `enums.py`, `api/queue.py` |
| 8 | T7-be | T3-be | `api/archives.py`, `services/archive.py`, `archive_media.py`, `tasks/media.py`, `models/printing.py` |
| 9 | T3-web | T3-be | `PrinterCard.tsx`, `FilamentsSection.tsx`, `StatusSection.tsx`, `usePrinterModals.tsx`, `printerControl.ts` |
| 10 | T6-web | T6-be | `LoginPage`, `api/auth.ts`, `OidcProvidersCard`, `TwoFactorCard`, Server-tab `SmtpCard` |
| 11 | T2-be | T1-be | printer hot files, `printerd.py`, `services/kprofiles.py` |
| 12 | T1-web | T3-web | card hot files, `DryingDialog`, Printer-tab `DryingSettingsCard` |
| 13 | T4-web | T4-be | `PrintDialog.tsx`, `QueuePage.tsx`, `api/queue.ts`, `QueuePreheatCard` |
| 14 | T7-web | T7-be | `PrintHistoryPage`, `RunDetailSheet`, `RecentRunsList`, `TimelapsePlayer`, `ArchivePurgeCard` |
| 15 | T8a | T6-be | `services/notify/*`, `EmailPrefsCard`, `models/notify.py` |
| 16 | T2-web | T1-web | card hot files, `KProfilesModal`, `SpoolForm` (K-profile section) |
| 17 | T8b-be + mounts | T7-be, T3-web, T8b-web | `api/archive_gcode.py`, `RunDetailSheet` toolpath tab, `FileManagerModal` preview |

Slot picture: items 1–5 start immediately. When T3-be merges, 6/7/8/9 become ready and fill slots as 2–5 finish. T5-be and T5-web are stitched by a small integration commit on the later branch (whichever merges second swaps mocks for the real contract). The queue never idles below 5 until fewer than 5 items remain.

**Shared files every item appends to** (`api/__init__.py`, `api/types.ts`, `en.json`, `celery_app.py` beat dict, `VIEWER_ALLOWLIST`, `_PATH_PARAM_VALUES`, alembic chain): append-only sections per phase; the controller merges those PRs one at a time and each branch rebases on main before its Opus review so conflicts resolve mechanically.

Per item: controller writes `docs/superpowers/plans/2026-10-24-t{n}[-be|-web].md` (API contract + tasks) → Sonnet implementer in its worktree → gates green → rebase → one Opus review + fix wave → PR → green CI → `gh pr merge --merge` → start next ready item. New backend deps: reportlab, qrcode[pil], pypdfium2, pyotp, apprise, aiosmtpd (dev) → relock `uv.lock`; web adds nothing except vendored JS (run `npm ci` in main-tree `web/` only if a lockfile changes).

## Rulings made
1. **Humidity**: `ams_units.humidity` already comes from `humidity_raw` % (bambu.py:382–384) → no migration; add `humidity_index` + ported fallback. Thresholds in %.
2. **Clear-plate**: flag always set on FINISH/FAILED; gates dispatch only when `queue.require_plate_clear` (default off); independent of review gate/`hold_reason`.
3. **Previous-success**: terminal `QueueState.SKIPPED` (`ALTER TYPE … ADD VALUE` in `autocommit_block`; downgrade maps → HELD) + `hold_reason="previous_failed"`; previous = latest terminal on same printer (batch-scoped if set); failed/aborted block, cancelled skipped over, none → run.
4. **Batches**: grouping row; dispatch fans out `quantity` entries `copies=1` via existing Fanout; ungroup nulls `batch_id`.
5. **Location FK**: migration creates `storage_locations` from distinct trimmed `spools.location` (`name_key=lower(trim)`), backfills, drops text; downgrade recreates; API keeps read-only `location` name.
6. **2FA × OIDC**: callback issues `oidc_2fa` ephemeral token → `/login?step=2fa` unless provider `trusts_idp_mfa` (new `oidc_providers` column, default false). `security.require_2fa`: login allowed but `must_enroll_2fa`; `require_session` 403 `2fa_enrollment_required` except `/auth/*`, `/me`, 2FA self-service. Admin reset only by another admin/CLI.
7. **LDAP dropped** (user decision): OIDC covers directory-backed logins; no `ldap3`, no auth-path branch. Existing `User.oidc_issuer/oidc_subject` already distinguishes local vs OIDC accounts, so no `auth_source` column is added.
8. **Tokens/purge**: pre-auth TTL 5 min, single use, 5 attempts, stored hashed; setup token 10 min. Purge deletes `print_archives` rows (blobs via storage GC), never `print_jobs`/ledger/queue; skips pending-confirm archives and `keep_*` classes.
9. **Print-from-SD included** (trivial via `start_print` `file:///sdcard/<path>`, `.3mf`/`.gcode.3mf`); `sd_print` `verified=True` only for A1 Mini after live check.
10. **Public confirm route**: GET renders prompt only, POST records; 32-byte urlsafe token, single use, 7-day expiry; both in `PUBLIC_ROUTES`; 30/min per IP.
11. **libvgcode**: vendor 17 KB renderer verbatim on existing `three`; lazy-loaded.
12. **Apprise instead of Slack kind**: one `apprise` provider covers Slack/Discord/Matrix/Gotify/etc.; existing Webhook untouched. Per-user email prefs ride the T6 mailer, so T8a waits for T6-be.

## Verification
- Per PR: `uv run pytest` (backend, sandbox off) + `npm run build` + `npm run lint` + `npm test` green locally and in CI; alembic single head; `test_auth_sweep`/`test_perm_sweep` green; migration test up→down→up.
- Per phase live-verify checklist (above) written to `docs/superpowers/plans/2026-10-24-t{n}-live-verify.md` for the user's A1 Mini; items not verifiable on A1 Mini are labelled and left `verified=False`.
- End of roadmap: update memory (`bambuddy-parity-v3-gaps` → status ledger; new `bambuddy-parity-v3-roadmap`), compose redeploy note (`IMAGE_TAG=edge … pull && up -d`).
