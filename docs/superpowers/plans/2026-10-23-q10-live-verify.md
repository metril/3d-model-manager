# Q10 Obico live-verify (A1 Mini)

Hardware: A1 Mini, camera=a1 (~1 fps, low-res JPEG) -- expect noisier scores than a 720p webcam.

## Setup
- [ ] `COMPOSE_PROFILES=...,obico` + `-f compose.obico.yaml`; `docker compose up -d obico-ml`; wait for healthy (first start loads the model, ~60 s).
- [ ] `printerd`/`api` can reach `obico-ml:3333`; the sidecar can reach `http://api:8000` (`TDMM_OBICO_CALLBACK`).
- [ ] Settings -> Printer -> Failure detection: URL `http://obico-ml:3333`, **Test connection** shows Connected. Save with enabled, interval 10 s, defaults warn 0.38 / pause 0.78, action Notify.
- [ ] Printer setup -> A1 Mini: toggle "Watch this printer" on, action = Notify first.

## Idle behaviour
- [ ] While not printing: no `obico-*` entry in the camera viewer ZSET (`ZRANGE printer:{id}:camera:viewers 0 -1`), no ML requests in the sidecar log.
- [ ] Printer detail page: **Test** (`POST /api/printers/{id}/detection/test`) on a healthy bed returns a score near 0.

## Healthy print
- [ ] Start a small known-good print. After ~30 frames (warmup, ~5 min) the badge on the printer page shows `AI 0.0x`; the viewer ZSET contains `obico-{id}`; at most one ML request per 10 s.
- [ ] Note the typical smoothed score across the print (record min/max): ____ / ____. No `failure_detections` rows.

## Deliberate spaghetti
- [ ] Start a print, then at layer ~5 pull the part off the bed (or print with a bed cooled / glue stick removed).
- [ ] Watch the badge climb; `warn` row appears in `GET /api/printers/{id}/detections` (action `warn`, with frame).
- [ ] Crossing the pause threshold records one `notify` row and fires `failure_suspected` (Q6 channel receives it once).
- [ ] Re-run with action = Pause: printer pauses via the normal command path (`cmd_result` request_id starts `obico:`), exactly one pause, Resume works afterwards.
- [ ] Tune with the 1 fps camera: if healthy-print max exceeds warn, raise warn/pause (try 0.5 / 0.9) or lengthen the interval; record final values: ____ / ____.

## Failure modes
- [ ] `docker compose stop obico-ml` mid-print: after 3 failed checks (~30 s) the badge reads "detection paused"; printing is unaffected; no restart of detection until the job ends or detection is toggled off/on.
- [ ] Restart `obico-ml`, toggle the printer off/on: detection resumes.
- [ ] printerd log shows no tracebacks from `printerd.obico`; MQTT status keeps updating throughout.
