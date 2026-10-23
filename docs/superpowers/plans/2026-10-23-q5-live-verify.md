# Q5 live verification (A1 Mini)

Needs the real printer on the LAN with LAN Mode + Developer Mode on.

1. Discovery: `POST /api/printers/discover` (no body) returns the A1 Mini with its
   serial and a `model_hint`; with `{"cidr": "<lan>/24"}` the sweep alone finds it
   (`source: "sweep"`). Bridged container: SSDP absent, sweep still finds it.
2. Diagnostic: `GET /api/printers/{id}/diagnostic` is all `ok` (`tcp_322` = skip for
   the A1). Unplug the printer: `tcp_8883/990/6000` flip to `fail` with hints and
   `tls_serial`/`mqtt_auth` become `skip`. Wrong access code -> `mqtt_auth` fail.
3. HMS: trigger a real fault (e.g. open the AMS lid mid-print or pull filament);
   `GET /api/printers/{id}/hms` decodes it with description + wiki URL. `hms/clear`
   dismisses it; one remedy from `actions` via `hms/action` behaves like the screen.
4. Firmware: `GET /api/printers/{id}/firmware` shows the real `current`; run the beat
   (`celery call app.tasks.firmware.check_firmware`) and confirm `latest`/notes.
5. Upload: only if `update_available` is true and `download_available` is true,
   `POST .../firmware/upload` stages the `.bin` in the SD root (verify on the printer's
   Firmware screen). Confirm nothing flashes until started on the printer.
