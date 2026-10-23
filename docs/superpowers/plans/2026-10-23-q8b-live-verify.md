# Q8b live verification (A1 Mini)

Prereq: rebuild/restart `api`, `worker`, `beat`, `printerd` (new migration runs on api start).

1. Samples: with the printer online, wait ~2 min, then
   `docker compose exec db psql -U tdmm -c "select ts,nozzle,bed,chamber,fan_part,ams from printer_sensor_samples order by ts desc limit 5"`.
   Expect a row about every 60 s; none while the printer is powered off (last_report_at stalls).
2. API: `GET /api/printers/{id}/sensors` returns <= 500 points, ascending `ts`;
   `?from=<1h ago>&step=300` returns ~12 points. `GET /api/printers/{id}/ams-history?unit=0`
   returns humidity/temp points (empty if no AMS). A user without print access gets 403.
3. Prune: insert an old row (`ts = now() - interval '40 days'`), run
   `docker compose exec worker celery -A app.tasks.celery_app call app.tasks.sensors.prune`;
   the old row is gone, recent ones remain. Change `sensor_history_days` via `PUT /api/settings/app` and re-run.
4. Stats: `GET /api/stats/dashboard?days=30` -> sane success rate, heatmap total == archive count,
   time-accuracy deltas within a plausible range, records match the archive list, per-printer hours.
5. Printerd: `docker compose logs printerd` shows no "sensor enqueue failed"; stop redis briefly and
   confirm the printerd poll loop keeps running.
