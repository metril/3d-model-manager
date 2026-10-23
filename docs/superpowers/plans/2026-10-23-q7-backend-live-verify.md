# Q7-backend live verification

1. **HA/Tasmota plug auto-off**: `PUT /api/printers/{id}/plug` with kind `ha` (`entity_id`, optional
   `power_entity_id`) or `tasmota` (`host`), `auto_off_after_min: 10`. Run a print; after FINISH and the
   nozzle < 50 C, the plug switches off ~10 min later (`GET .../plug` -> `last_state: off`). Heating the
   nozzle again or starting a print resets the window.
2. **Auto-on**: set `auto_on_before_print`, power the plug off, queue a READY item; plug turns on at the
   next dispatch tick and the printer comes online.
3. **Energy**: with a metering plug, finish a print and check the archive's `energy_kwh`/`energy_cost`
   (set `energy_cost_per_kwh` in app settings).
4. **Backup**: `POST /api/admin/backups/run`; `backups/<stamp>-<id>/{db.dump,settings.json}` exists in the
   storage backend; `pg_restore --list db.dump` works. With a git target configured, `git_pushed` is true
   and files appear under `tdmm-backups/` in the repo. Library scan does not adopt `backups/`.
5. **Prometheus**: mint an API token as an admin; `curl -H "Authorization: Bearer $T" /api/metrics`;
   add a scrape job with `authorization.credentials`; confirm `tdmm_printers`, `tdmm_plug_power_watts`.
6. **Update badge data**: `docker compose exec worker celery -A app.tasks.celery_app call app.tasks.updates.check`;
   `GET /api/system/update` shows latest/current/url/notes.
7. **Support bundle**: `GET /api/admin/support-bundle` -> zip with no secrets in `settings.json`.
