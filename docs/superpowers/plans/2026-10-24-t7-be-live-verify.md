# T7-be live verify (A1 Mini)

Prereqs: `TDMM_PUBLIC_URL` set (Settings -> Server) so confirm links are absolute; an ntfy/webhook notify channel subscribed to `print_confirm_request`; ffmpeg/ffprobe in the worker image.

1. Enable: `PUT /api/settings/confirm {"enabled":true,"external_prints":true}`.
2. Finish (or cancel->stop) a print on the A1 Mini. Within a minute the channel receives "How did <name> turn out?" with `good_url`/`bad_url` (`?tap=1`).
3. Open the link on a phone: page auto-submits, shows "Recorded". Re-open the link: 410. `GET /api/archives/{id}` shows `user_verdict`, `user_verdict_source: "link"`; a `bad` tap flips `result` to `failed` (`recorded_result` unchanged).
4. Defaults: set `default_outcome:"good", default_after_h:1`; leave one print unanswered; after the hourly beat (`archive.confirm_defaults`) verdict source is `api`.
5. Plate-clear (after T3-be merged): set `default_good_on_plate_clear:true`; press Clear plate in the UI; the pending archive gets verdict `good` / `plate_clear`.
6. Timelapse: `GET /api/archives/{id}/timelapse/info`, `/thumbnails?n=12`; `POST /timelapse/process` (`trim_start=2`, `speed=2`, `save_mode=new`) -> poll `/timelapse/jobs/{job_id}` to `done`; the new clip streams from `/photos/{filename}` and plays 2x.
7. Photos: upload a phone JPEG; confirm the served image has no EXIF (`exiftool`) and is <= 4096 px.
8. Compare two archives of the same model with different layer heights: `settings_diff` lists `layer_height`; `correlation` rates sensible.
9. Purge: `PUT /api/settings/archive_purge {"enabled":true,"older_than_days":1,...}`; `POST /archives/purge/preview` then `/run` match; `print_jobs`/Prints history unchanged; `GET /archives/purge/stats` shows the run. Disable again afterwards.
10. Calendar: `GET /api/archives/calendar?from=...&to=...` matches the history list.
