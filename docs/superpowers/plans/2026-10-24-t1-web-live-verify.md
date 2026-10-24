# T1-web live verify (A1 Mini, then any drying-capable printer)

A1 Mini (AMS lite) is drying-unsupported; drying UI on capable models is fixture/mock-verified only.

1. A1 Mini card: AMS unit header shows NO "Dry" button and no chip (`ams_drying` false).
2. `POST /api/printers/{id}/drying/start` on the A1 Mini returns 422 `unsupported`; UI never offers it.
3. Humidity shows as a relative %, `--` when the unit reports only an index.
4. Settings -> Printer: "AMS drying" card loads seed presets (PLA 45/45/12/12 ... PVA 65/85/12/18); edit one, Save, reload: persisted. Out-of-range values disable Save with a message.
5. Settings -> Printer: "Scheduled drying" list is empty (no schedules); no console errors.
6. On a drying-capable printer (X1E/H2D/P2S with AMS 2 Pro/HT), if available:
   - Dry button opens the dialog; filament defaults to the loaded tray type, preset buttons fill temp/hours (HT uses n3s).
   - Start now: chip shows Checking -> Drying with a countdown that matches the printer screen; Stop returns the unit to idle.
   - At time / After current print create a row in the dialog's "Scheduled" section; Cancel removes it.
   - Blocked (cable unplugged, filament at outlet): reason panel lists the message and Start (Now) is disabled.
   - P1P/P1S: chip shows state with tooltip "Start from the printer screen", no Dry/Stop buttons.
