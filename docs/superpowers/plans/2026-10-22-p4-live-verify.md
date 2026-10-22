# P4 live-verify (A1 Mini, camera & timelapse)

- [ ] Auth packet and port 6000 on current firmware (Developer/LAN mode,
      10.10.76.70): frames arrive and `status=live`.
- [ ] Native fps and frame size; confirm the 5 fps cap (`MIN_FRAME_INTERVAL_S
      = 0.2` in `camera_supervisor.py`) holds and that two tabs share one
      upstream (`ss -tn dport = :6000` in printerd shows a single connection).
- [ ] Whether a second concurrent raw :6000 client (outside the app) is
      refused by the printer.
- [ ] Thread stops ~30 s after the last tab closes (`IDLE_STOP_S = 30`).
- [ ] Snapshot endpoint goes 503 → JPEG once the camera is live.
- [ ] Timelapse directory on the printer: `/timelapse` vs `/timelapse/video`
      (`TIMELAPSE_DIRS` in `archive_media.py`), file format/naming (`.avi`
      vs `.mp4`, `_TIMELAPSE_EXTS`).
- [ ] FTP `LIST` timestamp timezone: code treats parsed mtimes as UTC
      (`datetime(..., tzinfo=UTC)` in `bambu.py`) — confirm against the
      real clock/timezone the A1 reports.
- [ ] Whether timelapse requires an SD card, and how long after FINISH the
      file appears on disk (tune `_TIMELAPSE_FETCH_DELAY_S = 90` /
      `_TIMELAPSE_RETRY_DELAY_S = 120` in `tasks/archive.py` if off).
- [ ] The raw `project_file` payload (`project_file_payload` in `bambu.py`)
      is accepted by the printer with `timelapse`/`bed_leveling` set
      independently, and both are honoured on the touchscreen.
- [ ] `_publish_raw` depends on vendored `bambulabs_api`/`paho-mqtt`
      internals — note the pinned `paho-mqtt` version in `backend/uv.lock`
      (2.1.0) in case a firmware/lib drift changes the wire format.
- [ ] Run snapshot + MP4 attach on a real completed print (archive row gets
      both `snapshot` and `timelapse` media).
- [ ] ffmpeg present in the rebuilt worker-cpu image after `docker compose
      build` (transcode AVI → MP4 actually runs, not a no-op).
- [ ] If a reverse proxy sits in front of the API, the camera token/stream
      path (`/printers/{id}/camera/...`) still works through it (no
      buffering of the MJPEG stream, auth header/query token passed through).
