# T2-be live verification (A1 Mini + AMS Lite; H2D fixture-only)

Prereq: printerd running, printer online, idle, Developer Mode on. Calls below via the API or the T2-web modal.

1. **List @0.4**: `GET /api/printers/{id}/kprofiles?nozzle=0.4&refresh=1` -> profiles match Studio's Calibration > PA list (name, K, filament, `cali_idx`). Per-item `nozzle_diameter` should equal the installed nozzle (envelope fallback). Repeat without `refresh` -> instant (cache, 10 min).
2. **Create**: `POST kprofiles {name, filament_id:"GFA00", k_value:0.025}` -> `payload.setting_id` is `PF`+17 digits; refresh list shows it; no `invalid tray_id` rejection in printerd log.
3. **Rename/edit**: `POST kprofiles` with the `setting_id` (+ `cali_idx`) and a new name/K -> list shows the change in place (same `cali_idx`, no duplicate).
4. **Note**: `PUT kprofiles/{setting_id}/note`; reappears in the list `note`.
5. **Delete**: `DELETE kprofiles/{setting_id}?nozzle=0.4` -> profile gone after refresh (X1/A1 variant: `setting_id`). While a print runs: set/delete/select -> 409 `printing`; list still works.
6. **Link to spool**: `PUT /api/spools/{id}/kprofiles [{printer_id, nozzle_diameter:0.4, k_value, cali_idx, setting_id}]`; assign the spool to an AMS slot with device apply -> slot's `cali_idx` in `GET status` becomes the profile's (select chained after configure).
7. **Drift**: with the spool linked, power-cycle the printer. First idle push after reconnect: tray `cali_idx` -1 -> printerd re-sends `extrusion_cali_sel` (audit row `kprofile.reapply`, `GET status` shows the restored `cali_idx`). A second push within 10 min does nothing. Pick a *different* profile in Studio -> it is left alone.
8. **Before send**: lose the selection again (power-cycle), queue a print -> `cali_idx` is restored before the project_file is sent.
9. Confirm the tray fields `cali_idx` / `k` / `tray_info_idx` are actually present in the AMS push; if the A1 uses other key names, fix `bambu._tray_entry` (single place).
10. **H2D** (`verified=False`): delete variant (`extruder_id`+`nozzle_id`+`nozzle_diameter`, no `setting_id`) is covered by payload goldens only; nozzle->extruder tray mapping is unknown, so the fixer uses the lowest stored extruder.
