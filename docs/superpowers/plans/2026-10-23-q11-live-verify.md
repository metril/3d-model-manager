# Q11 live verify (A1 Mini + Bambu Studio)

Prereqs: PR merged/deployed, `.env` has `TDMM_VP_ENABLED=true`, `DATABASE_URL`/`REDIS_URL` reachable from the host
(the service is `network_mode: host`), ports 2021/udp, 8883/tcp, 990/tcp and 50000-50009/tcp free on the docker host.

1. `docker compose --profile virtual-printer up -d --build virtual-printer`; `docker compose logs virtual-printer` shows
   "no enabled virtual printer; idle". With a port taken (e.g. real printer tooling on 990) it must exit 2 with a clear message.
2. Settings -> Printer -> Virtual printer: create "Virtual A1 Mini" (model A1 mini, mode Review). Note the access code.
   Log shows `virtual printer 'Virtual A1 Mini' ... up` within 10 s.
3. Bambu Studio on the same LAN: Device tab lists "Virtual A1 Mini" (SSDP). If not, add by IP + serial + access code.
   Connecting succeeds (no "connection failed"); the panel shows an idle printer.
4. Slice any model for A1 mini, press Send -> Virtual A1 Mini. Studio finishes the upload without error.
5. tdmm: Queue -> Review inbox shows the entry (model auto-created from the file name, plate 1). Approve -> it stays READY
   (no real printer); switch the mode to Queue and resend -> entry appears READY without review.
6. Mode Archive: resend -> model/file appear in the library, no queue entry. Studio "Upload only" (no print) is archived too after ~30 s.
7. Proxy: set mode Proxy + target the real A1 Mini (idle). Send from Studio -> the dispatcher prints on the real A1 Mini with the
   AMS mapping planned from its live trays (check the printer card and the archive row afterwards). Studio's own AMS choice is ignored.
8. Edit the model or regenerate the access code; within ~10 s the service restarts and Studio must use the new serial/code.
9. Cert: `${DATA_DIR}/virtual-printer/certs/<serial>.crt` exists, survives a container restart (same fingerprint).
10. Real-printer regression: printerd and the real A1 Mini card are unaffected while the service runs.
