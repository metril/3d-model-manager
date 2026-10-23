# Q5-web live verify (A1 Mini, needs PR #20 deployed)

- [ ] Settings -> Printer: Scan finds the A1 Mini; clicking it fills name/host/serial/model.
- [ ] Add printer with a wrong access code: preflight lists mqtt_auth failure; Back returns, Save anyway saves.
- [ ] Unplug/wrong host: failed port check shown with hint; Run diagnostic works on a saved printer (blank code) and ad hoc.
- [ ] Detail page: Info shows IP, serial, firmware, dev mode, nozzle, total hours; "untested on this model" absent for A1 Mini.
- [ ] Trigger a real HMS fault: "HMS (n)" button appears; modal decodes description, wiki link opens, Clear empties it.
- [ ] Firmware: current version shown; if an update exists, "Stage to SD card" puts file on SD; flash only starts from printer screen.
- [ ] No firmware download available: Stage shows the 409 message.
