# Bambuddy parity v2 — roadmap Q1–Q11

## Context
P1–P8 (PRs #1–#8) gave us auth, archive, printer-first shell, A1 camera, spools, slicer, billing, HA. Our library/project management beats bambuddy. The printer side still lags visibly. Gap analysis (upstream clone at scratchpad `upstream/bambuddy`, commit 98ecaf5 2026-10-22) found:

- **Printer card/dashboard**: no model image, sizes, summary bar, filters/sort, print thumbnail, temp/fan tiles, controls, AMS unit cards, badges (WiFi/HMS/firmware/door), file browser, skip objects.
- **Control**: only pause/resume/stop. `bambulabs_api` (already wrapped in `backend/app/printers/bambu.py`) exposes temps, fans, light, speed, home, Z move, gcode, skip objects, load/unload, calibration, firmware. Missing in lib: airduct, chamber heater, AMS drying, K-profiles, XY jog (use G-code).
- **Camera**: A1 port-6000 only; no RTSP (X1/P1S/H2), window, camwall, overlay, zoom/rotation.
- **Platform**: no notifications, HMS catalog, firmware, diagnostics, discovery scan, maintenance, smart plugs, backups, update check, metrics, stats widgets, themes, i18n, PWA, shortcuts, Obico, virtual printer, Spoolman.

**Premise correction**: bambuddy has **no cloud MQTT**. It connects to the printer's local 8883 broker regardless of LAN-Only mode, detects Developer Mode (X1/H2: `fun & 0x20000000 == 0`; A1/P1: harmless `ams_filament_setting` echo 5 s after connect — `success` ⇒ on, "mqtt message verify failed" or HMS MQTT_VERIFY_FAILED ⇒ off), and shows a global banner "Developer LAN mode is not enabled on: X. Some features may not work." Status/camera/FTPS work without Dev Mode; commands are rejected. We match that behaviour (Q2).

## User decisions (2026-10-23)
- Card = bambuddy expanded-card layout 1:1 incl. the printer PNGs from bambuddy `frontend/public/img/printers/`. **The user is the original author of bambuddy**, images and code alike. Copying, adapting and improving bambuddy code, data files and assets is permitted and has no licence impact on this repo: no attribution, no NOTICE, no diff policing. This supersedes the "no code copy" rule in the 2026-10-21 spec. Prefer porting proven upstream logic (adapted to Postgres/Celery/printerd and our permission model) over reinventing it.
- Keep `/printers/$id` = XL card + big camera + per-printer history/stats.
- Scope ALL: notifications; HMS+firmware+diagnostics+discovery; maintenance+plugs+backups+updates; camwall/overlay/stats/themes/i18n/PWA; **Obico, virtual printer, Spoolman** (reverses TABLED).
- Constraints: reuse `bambulabs_api` where it has the method, else raw MQTT via `_publish_raw` (bambu.py:80), porting upstream `bambu_mqtt.py` payloads where needed (airduct, drying, K-profiles, HMS clear, dev-mode probe); P1 permission enum only; every new table `created_by_user_id`; `tests/test_perm_sweep.py` green; Bambu Studio slicer stays.

## Cross-cutting rules
- **Command envelope v2** (Q2): `_publish_command` (`backend/app/api/printers.py:429`) sends `{command,args,request_id}`; printerd replies on Redis `printer:{id}:cmd_result` (30 s TTL). API waits ≤3 s → `200 {ok}` | `409 dev_mode_off` | `422 unsupported` | `202 pending`. Bare-string commands keep working.
- **Capability table** `backend/app/printers/capabilities.py`: model_key (normalised `model`, serial-prefix fallback) → `chamber_heater, chamber_temp, airduct, door, dual_nozzle, aux_fan, chamber_fan, camera(a1|rtsp|none), lidar, max_bed, max_nozzle, ams_drying, verified`. Served as `PrinterOut.capabilities`; web hides, API 422s; `verified=False` adds the "untested" tooltip (decision 4). A1 Mini: all chamber/airduct/door/dual/aux false, camera=a1, verified=True.
- One PR per phase, own alembic revision + `test_migrations_q{n}.py`. Implement with Sonnet, final whole-branch review with Opus, merge, next phase (autonomous, no continue prompts).
- First commit of Q1 also adds the spec `docs/superpowers/specs/2026-10-23-bambuddy-parity-v2-design.md` (this file's content) since plan mode cannot write it.

## Q1 Images, capabilities, status schema v2
- `capabilities.py`, `printers/stages.py` (stg_cur → text; port upstream's stage table), `model_key()`.
- Extend `PrinterPublicState` (`printers/base.py`) + `PrinterStatusOut` (`schemas/printers.py`), all optional: `nozzle_target, bed_target, chamber_temper, chamber_target, fan_part_pct, fan_aux_pct, fan_chamber_pct` (0–15 → %), `light_on, speed_level, stage, hms[{attr,code}], sdcard, nozzle_type, firmware{ota,ams[]}, ams_units[{id,humidity,temp,serial,fw}], ext_tray, print_thumbnail_key, ipcam, dev_mode(bool|null), dev_mode_reason, last_report_at`. Fill in `BambuLanAdapter._snapshot` (bambu.py:315) via lib getters, else `mqtt_dump()`.
- `PrinterOut.total_print_hours` from archives (`services/printer_overview.py`).
- Web: copy PNGs → `web/public/img/printers/` (user's own images, no NOTICE); `printerImage(modelKey)` explicit table in `web/src/lib/printerState.ts`.
- Tests: `test_bambu_adapter.py` (recorded A1 Mini pushall fixture in `backend/tests/printer_fixtures.py`), `test_printer_status_api.py`, `test_capabilities.py`, `printerState.test.ts`.
- Live: `/status` shows targets, part-fan %, light, stage, firmware; chamber null.

## Q2 Command bus v2, controls, Developer Mode
- printerd `handle_command` (`printerd.py:242`) → dispatch table w/ arg schemas, publishes `cmd_result`; refuses `jog/home/extrude` while RUNNING/PAUSE.
- Adapter methods (default `NotImplementedError` in base): `set_temperature(nozzle|bed|chamber)`, `set_fan(part|aux|chamber,pct)`, `set_light`, `set_speed(1–4)`, `home`, `jog(axis,mm)` (Z lib; XY `G91/G0/G90`), `extrude(mm)`, `send_gcode`, `skip_objects`, `load/unload(tray)`, `calibrate(opts)`, `set_airduct` + `clear_hms` via `_publish_raw`.
- `printers/devmode.py`: per-worker state machine following decision 2 (fun-bit → guarded idle echo probe → lazy detection; `null` = unknown). Stored in Redis `PrinterPublicState.dev_mode`. API short-circuits 409 when false (pause/resume/stop still attempted).
- New table `audit_log(id, user_id, printer_id, action, payload JSONB, created_at, created_by_user_id)`; gcode/jog/extrude/skip-objects/firmware-upload write a row. Motion guards per decision 3.
- Routes: `POST /printers/{id}/control/{temperature|fan|light|speed|home|jog|extrude|gcode|airduct|calibrate|hms/clear}` (`require_printer_access("control")`; gcode additionally admin-only), `GET /printers/{id}/objects` + `POST /printers/{id}/skip-objects` (objects from archived 3MF via `printers/gcode3mf.py`), `GET /printers/developer-mode-warnings` (session, `visible_printer_ids`).
- Tests: `test_printerd.py`, `test_bambu_adapter.py` (lib mocks), `test_devmode.py`, `test_printer_control_api.py`.
- Live A1 Mini: bed→60 updates target; light toggle; Z jog idle; Sport while printing; Dev Mode off ⇒ banner + 409s, status live.

## Q3 Bambuddy-layout card + dashboard (main payoff)
- Split `web/src/components/print/PrinterCard.tsx` → `components/print/card/`: `CardHeader` (image, name, nozzle size, hours, menu Edit/Info/Reconnect/Force refresh/Diagnose/Delete), `BadgeRow` (connection, WiFi dBm, HMS count, queue count, firmware, door), `StatusSection` (96px thumbnail, stage, name, progress, remaining, ETA, layer, started-by, skip-objects; idle "Last: …", plate-clear pill), `TempFanTiles` (capability-gated; popover input + presets PLA 220/60, PETG 250/80, off; `current → target`; optimistic, revert after 10 s), `ControlsSection` (light, speed segmented, movement popover XY/Z step 1/10/50 + home + extrude/retract disabled while printing, calibration, Pause/Resume/Stop via `components/ConfirmDialog.tsx`), `FilamentsSection` (AMS unit cards humidity/temp/hover; slot circle + fill bar + loaded + mapping pill; menu load/unload/assign spool reusing existing assign; external spool card), `CardFooter` (camera overlay|window, Print → `PrintDialog.tsx`, plug slot for Q7).
- Sizes S/M/L/XL (localStorage; grid 5/3/2/1; scale 1/1/1.2/1.4; S = compact).
- `PrintDashboardPage.tsx`: `StatusSummaryBar` (counts + next-available ETA, client-side from `/printers/overview`); filters status/hide-offline/search + sort name/status/model/ETA w/ group headers in URL params (`printHistorySearch.ts` pattern); bulk select pause/stop; drop-file-on-card → queue (`lib/droppedFiles.ts`).
- `DevModeBanner` in `components/AppShell.tsx` (wiki link). `PrinterDetailPage.tsx` = XL card + big camera + `PrintJobHistory` + `HistoryStats`.
- Backend: skip-objects plate image reuses archive thumbnail.
- Tests: vitest per section w/ A1 Mini gating fixture; update `PrinterCard.test.tsx`, `PrintDashboardPage.test.tsx`.
- Live: visual match; fan popover 50 % spins; skip-objects on multi-object print.

## Q4 Camera v2
- `printers/camera_rtsp.py` `CameraSource`: port upstream `services/camera.py` RTSP path (`rtsps://bblp:{code}@host:322/streaming/live/1`, ffmpeg `-rtsp_transport tcp` → MJPEG) → JPEG splitter. `open_camera` picks by `capabilities.camera` (drops X1 NotImplemented at bambu.py:461). `camera_supervisor.py` unchanged. Ensure ffmpeg in Dockerfile.
- External camera `options.camera={kind:mjpeg|rtsp|snapshot,url_enc}` → `ExternalCameraSource`; rotation in `options`; `fps` query param.
- Overlay: table `overlay_tokens(id,printer_id,token_hash,created_by_user_id,revoked_at)`; `POST /printers/{id}/overlay-token` (control); `GET /overlay/{id}?token=` public (allowlist in auth sweep).
- Web: rewrite `CameraTile.tsx` (zoom/pan/pinch/rotation/fullscreen/light; 5 s stall → backoff reconnect; stop on unmount). Routes outside shell (like `/viewer/$slug`): `/camera/$id`, `/overlay/$id`, `/camwall` (max-live + snapshot interval).
- Tests: `test_camera_rtsp.py` against a `mediamtx` rtsps test-pattern container (decision 5; CI service + local `compose.test.yaml` profile), `test_camera_supervisor.py`, `test_camera_api.py`, `CameraTile.test.tsx`.
- Live: rotation/zoom/window/camwall/overlay on A1 Mini. RTSP transport verified in CI; Bambu URL constants labelled beta until an X1/P1S user confirms.

## Q5 HMS, firmware, diagnostics, discovery
- HMS: copy upstream catalog JSONs + port lookup/actions logic per decision 8 into `services/hms.py`; `GET /printers/{id}/hms` (print), `POST …/hms/clear|action` (control).
- Firmware: `services/firmware.py` daily beat → Setting cache; `GET /printers/{id}/firmware`; `POST …/firmware/upload` (`settings:write` + control) Celery download → FTPS to SD (extend adapter FTP helper). Flash confirmed on printer screen only.
- Diagnostics: `probe.py` `staged_probe` → structured report (TCP 8883/990/6000/322, TLS serial, MQTT auth, dev mode, container network mode, subnet match); `POST /printers/diagnostic` (`settings:write`).
- Discovery: `discovery.py` + SSDP (UDP 2021) + /24 TCP-8883 sweep (bounded concurrency); `POST /printers/discover` (`settings:write`).
- Web: `HmsModal`, `FirmwareModal`, `DiagnosticModal`, `PrinterInfoModal`; `PrinterSetupCard.tsx` Discover list + "Save anyway".
- Tests: `test_hms.py`, `test_firmware.py` (cassette), `test_diagnostic.py`, `test_discovery.py`.
- Live: discovery finds A1 Mini; unplug → failed port; real HMS decodes; firmware version shown.

## Q6 Notifications
- Tables `notification_channels(kind,name,config_enc,enabled,events[],quiet_hours,digest_cron,…)`, `notification_templates(event,title_tpl,body_tpl,…)`, `notification_log(…)`.
- `services/notify/` one module per provider: ntfy, pushover, telegram, discord, webhook, email, callmebot, bark, homeassistant (add `call_service` to `services/ha_client.py`). Sandboxed Jinja templates.
- Events: emitter in printerd `_transition_active_job` (started/finished/failed/paused, HMS new, dev mode off, filament low) → Redis list; queue/review events from API. Celery `notify.deliver` (retries), `notify.digest` (beat). Photo = latest `camera_state` frame.
- Routes `/notifications/*` (`settings:write`; test-send POST; own subscriptions session-only).
- Web: Settings → Notifications (channels, templates + preview, log).
- Tests: `test_notify_providers.py` (httpx mocks), `test_notify_events.py`, `test_notify_api.py`.
- Live: finish on A1 Mini → ntfy with photo; HMS notification.

## Q7 Maintenance, smart plugs, backups, updates, metrics
- Maintenance: `maintenance_tasks`, `maintenance_logs`; due from archive hours; per-model defaults from capabilities; `printers.maintenance_mode` → dispatcher skips (`services/dispatcher.py`); routes control.
- Smart plugs: `smart_plugs(printer_id,kind tasmota|ha|mqtt|rest,config_enc,auto_off_after_min,…)`; beat auto-off when idle & nozzle <50 °C; energy → `services/costing.py`; HA kind reuses HaLink entity picker.
- Backups: Celery `backup.run` (`pg_dump` + settings JSON → storage backend; optional git push GitHub/GitLab/Gitea/Forgejo); `backup_runs`; restore admin-only.
- Update checker: GitHub releases, daily cache, TopBar badge. `GET /metrics` Prometheus (API-token bearer, allowlisted). `GET /admin/support-bundle` admin.
- Web: Maintenance tab on detail page, plug row in card footer, Settings cards Backups/Updates.
- Tests: `test_maintenance.py`, `test_smart_plugs.py`, `test_backup_task.py`, `test_metrics.py`.
- Live: plug off 10 min after print; backup in storage; Prometheus scrape.

## Q8 Stats, themes, i18n, PWA, shortcuts
- Stats: extend `api/stats.py` + `services/stats.py` (success rate, time accuracy, failure analysis, activity heatmap, records, per-printer, filament trends). Table `printer_sensor_samples(printer_id,ts,nozzle,bed,chamber,ams_humidity JSONB)` sampled 60 s by printerd, pruned 30 d — backs Q3 HeaterHistory/AMSHistory modals (buttons hidden until Q8).
- Web `/stats` draggable widgets (dnd-kit), layout per user; follow `ui/sparkline.tsx`, `ui/stat-tile.tsx`.
- Themes: `ThemeToggle.tsx` → mode × style(classic/glow/vibrant) × accent via CSS vars in `index.css`.
- i18n: react-i18next; wrap shell/cards/settings first; ship `en` + scaffolding + CI missing-key check (no bulk translations).
- PWA: manifest, icons, SW for app shell only (never `/api` or camera). Shortcuts 1–9 nav in `useHotkeys` + `ShortcutsDialog`. External sidebar links in settings → `navItems.ts`.
- Tests: `test_stats_api.py`, `test_sensor_history.py`, widget vitest, i18n key check.

## Q9 Spoolman connector
- `spools.spoolman_id` (nullable unique), `spoolman_sync_state(spool_id,last_pushed_at,last_pulled_weight,…)`.
- `services/spoolman.py` (httpx): pull weight (Spoolman authoritative once linked); push on `SpoolUsage` insert (`services/spool.py`) → `PUT /spool/{id}/use`; AMS assignment → Spoolman location `printer:slot`. Config URL + mode `off|mirror|authoritative` in app_config; routes `inventory:write`; beat `spoolman.reconcile` 5 min.
- Web: Spoolman settings card, link/unlink on `SpoolsPage.tsx`, slot-chip badge.
- Tests: `test_spoolman.py` cassettes (push, conflict, off=no-op). Live: Spoolman container; A1 Mini print reduces remaining weight.

## Q10 Obico failure detection
- `obico-ml` sidecar in compose under profile (like `compose.slicer.yaml`).
- `printers/obico_watcher.py` thread in printerd beside camera supervisor: while RUNNING + enabled, frame every N s from `camera_state` (registered viewer) → ML `/p/`; EWMA smoothing; warn/pause thresholds; actions `notify|pause|none` via command path (`request_id` origin `obico`).
- Table `failure_detections(printer_id,print_job_id,score,frame_blob_key,action,…)`; event `failure_suspected` (Q6); BadgeRow badge. Routes: per-printer toggle control, global `settings:write`.
- Tests: `test_obico_watcher.py` (fake ML, smoothing — single spike never pauses), `test_printerd.py` pause path. Live: deliberate spaghetti on A1 Mini (low-res ~1 fps → tune thresholds).

## Q11 Virtual printer
- `backend/app/virtual_printer/` as its own compose service (never in printerd), feature-flagged in `api/features.py`: SSDP announce UDP 2021; MQTT 8883 self-signed (amqtt or embedded mosquitto — user approves dependency); answers `pushall` idle; FTPS 990 (pyftpdlib TLS) receives 3MF; `project_file` → intake via `services/slicer_intake.py`. Modes `queue|review|archive|proxy` (proxy → dispatcher w/ AMS mapping).
- Table `virtual_printers(id,name,model,serial,access_code_enc,mode,target_printer_id,…)`; routes `settings:write`. Networking, port pre-flight, cert volume and `TDMM_VP_PORT_*` per decision 7.
- Tests: `test_virtual_printer.py` (paho + ftplib vs in-process server on ephemeral ports → queue entry). Live: Studio "Virtual A1 Mini" send → review inbox; proxy prints on real A1 Mini.

## Dependencies
Q1→Q2→Q3 critical path. Q4, Q5 need Q1–Q2. Q6 needs Q2 emitter + Q5 HMS. Q7 hooks into Q3 footer. Q8 backfills Q3 history modals. Q9, Q11 independent. Q10 needs Q4+Q6.

## Parallel execution (controller = Fable; implementers = Sonnet, max 5 simultaneously; reviews/plans = Opus)
- Each implementer works in its own `git worktree` on its own branch (`q{n}-<slug>`), never in the main tree, never `git stash`/checkout in a shared tree. Rebase on `main` before PR.
- Phase = PR. One final Opus review per branch (no per-task reviewers). Controller runs the full backend suite in background; implementers run targeted tests only.
- Large phases split into backend + web agents working in parallel against an API contract the controller writes into the phase plan first (route paths, schemas, perms). Web agent mocks the contract until the backend branch merges.
- Waves (each wave ≤5 agents; next wave starts as its dependencies merge, not when the whole wave finishes):
  - **Wave A** (no deps): Q1-backend (capabilities/status v2/stages), Q1-web (images + `printerImage`), Q11 virtual printer, Q9 Spoolman, Q8a themes/PWA/shortcuts/external links.
  - **Wave B** (after Q1): Q2 command bus + controls + dev mode, Q5-backend HMS/firmware/diagnostics/discovery, Q4-backend camera RTSP/external/overlay tokens, Q7-backend maintenance/plugs/backups/updates/metrics, Q8b sensor-history table + sampling.
  - **Wave C** (after Q2): Q3-web card + dashboard + banner, Q4-web CameraTile/window/overlay/camwall, Q5-web modals + discovery UI, Q6 notifications (needs Q5 HMS merged), Q7-web maintenance tab + settings cards.
  - **Wave D** (after Q3, Q4, Q6): Q10 Obico, Q8c stats dashboard + i18n wrap of shell/cards/settings, Q7 plug row + Q8 history modals wired into the card.
- Controller dispatches the next ready item the moment a slot frees; never waits for a whole wave. Merge order follows dependency order; conflicts resolved by the implementer on rebase.

## Risks → decisions (all resolved; implementers follow these, no re-deciding)
1. **Licensing.** Not a risk: bambuddy is the user's own work. Implementers may copy and adapt upstream code, JSON catalogs, fixtures and images freely; the only rule is that ported code must fit our stack (async SQLAlchemy/Postgres, Celery, printerd command bus, P1 permission enum) and our test conventions.
2. **Dev-mode probe must never mutate the printer.** Order of detection: (a) `fun` bitmask if present in the report (X1/H2) — no probe; (b) otherwise the echo probe runs only when `gcode_state` is IDLE/FINISH, 5 s after connect, and only if the chosen slot's full tray record (`tray_info_idx, tray_type, tray_color, nozzle_temp_min/max`) is present in the last report; it echoes those exact values; if any field is missing or the printer is printing, no probe is sent and `dev_mode` stays `null` ("unknown", grey banner) until (c) lazy detection: the first user command's reply (`success` ⇒ on, reason containing `verify` ⇒ off) or the HMS MQTT_VERIFY_FAILED code. Q2 live-verify step: after the probe, slot values on the printer screen are unchanged.
3. **Raw G-code and motion safety.** `control/gcode` is admin-only, ≤1 KB, logged to an `audit_log` row (new table, Q2), refused while RUNNING/PAUSE. `jog` clamps step to {1,10,50} mm and refuses moves outside `build_volume_mm`; `extrude` requires nozzle ≥170 °C (printer refuses anyway; we fail fast with 409 `cold_extrude`). All three refused server-side while printing, in addition to the UI disable.
4. **Capabilities we cannot test on the A1 Mini** (chamber heater/temp, airduct, door, dual nozzle, aux/chamber fan, AMS drying, RTSP, X1 `fun` bit). `capabilities.py` carries `verified: bool` per model; non-A1 models ship `verified=False`. Unverified controls still render but carry an "untested on this model" tooltip and, on error, a "copy diagnostic" button that dumps the command, reply and last report. Unit fixtures for those models are taken from upstream's test fixtures and recorded reports (`tests/` in the bambuddy clone) where they exist, otherwise from Bambu's public MQTT field documentation (OpenBambuAPI). A Settings → Printer toggle lets a user mark a model verified after they confirm it.
5. **RTSP camera path is testable without an X1.** CI runs a `mediamtx` container serving a test pattern over `rtsps://` with a self-signed cert; `test_camera_rtsp.py` asserts ffmpeg connects with `-rtsp_transport tcp`, tolerates the untrusted cert, and the splitter yields valid JPEGs at the requested fps. Only the Bambu URL path `/streaming/live/1` and port 322 remain unverified; those are constants in one place (`camera_rtsp.py`) and labelled beta in the UI until verified.
6. **printerd load.** Sensor sampling (Q8) adds no polling: a 60 s timer writes the already-held last snapshot to Postgres via a Celery task, never from the MQTT thread. The Obico watcher (Q10) runs in its own thread with a 1-frame queue, ≤1 frame per 10 s, and auto-disables for that printer after 3 consecutive ML errors (badge shows "detection paused"). Both follow the camera supervisor's isolation rule: exceptions never propagate to the worker loop.
7. **Virtual printer ports/TLS.** Own compose service with `network_mode: host`, feature-flagged, last phase. On startup it checks 2021/udp, 8883/tcp, 990/tcp are free and exits non-zero with a clear message if not. TLS: self-signed cert generated at first start into a named volume (Studio does not pin LAN certs; bambuddy's own cert works the same way). Access code is random 8 digits shown in Settings. Integration test uses paho + ftplib against the in-process server on ephemeral ports (ports are configurable via `TDMM_VP_PORT_*` for tests).
8. **HMS catalog source.** Copy upstream `backend/app/data/hms_catalog.json`, `hms_catalog_extra.json` and `hms_actions.json` into `backend/app/data/`, and port `services/hms_errors.py` + `hms_actions.py` lookup logic. Keep an optional `scripts/build_hms_catalog.py` refresh from Bambu's public HMS endpoint for future codes. Unknown codes render as the raw code with a wiki link, never a crash.
9. **Spoolman reverses a TABLED decision.** `mode=off` is the default; with it off, no column other than `spoolman_id` is touched and no beat task runs, so existing users see zero behaviour change.

## Verification (per phase)
- Targeted backend tests only per implementer (full suite ≈11 min runs in background by controller): `cd backend && timeout 600 .venv/bin/pytest tests/<files> -q`.
- Web: `cd web && npm run build && npm test -- <files>` + oxlint baseline.
- `tests/test_perm_sweep.py` + `test_migrations_q{n}.py` green.
- Live-verify checklist `docs/superpowers/plans/2026-10-23-q{n}-live-verify.md` on A1 Mini (items above) → PR → green CI → merge → next phase.
