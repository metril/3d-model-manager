# Q2 live verify (A1 Mini)

- [ ] Bed target to 60: `POST /control/temperature {"target":"bed","value":60}` returns 200 and status `bed_target` becomes 60.
- [ ] Light toggle: `POST /control/light {"on":true}` then false.
- [ ] Z jog while idle: `POST /control/jog {"axis":"Z","mm":10}` moves; the same call while printing returns 409 `printing`.
- [ ] Sport speed while printing: `POST /control/speed {"level":3}` returns 200.
- [ ] Dev Mode off on the printer: `GET /printers/developer-mode-warnings` lists it, controls return 409 `dev_mode_off`, pause/resume/stop are still attempted, status stays live.
- [ ] After the probe (5 s after connect while idle) the external-slot values on the printer screen are unchanged. The probe uses tray_id 0, ported from upstream; check the A1 Mini accepts it.
- [ ] Cold extrude (nozzle < 170 C) returns 409 `cold_extrude`.
- [ ] `audit_log` has rows for gcode, jog, extrude and skip-objects.
