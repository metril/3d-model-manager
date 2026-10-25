# T2-web live verify (A1 Mini, real hardware)

1. Card footer shows "Pressure advance..." (control perm, `kprofiles` cap). Hidden for view-only users.
2. Open it: current nozzle tab first; list loads (first load may show "Waiting for the printer..." while the 202 poll runs; error after 15 s).
3. Refresh forces `refresh=1`; edit K/n/name (K outside 0-2 blocked), Save; add a new profile; rename; delete (confirm). Re-open: values persist on the printer / Studio.
4. Edit a note, blur: persists across reopen; clear it: removed.
5. Start a print: Add/Edit/Delete/note/Apply disabled with hint; list and Refresh still work.
6. Slot menu "Pressure advance...": its current profile is badged "Current"; "Set for this tray" changes the tray's K (status shows new `cali_idx`/`k_value` within one push). Check external spool (ams_id/tray_id for ext slot) - may need backend mapping.
7. "Link to spool..." then open that spool in Inventory > Edit: the K-profiles section lists printer/extruder/nozzle/K; Remove works; "Add from printer..." adds.
8. Configure slot with a K-profile chosen: slot saved, then profile selected. With a spool-stored profile, backend chains it (hint shown).
9. Power-cycle the printer: `cali_idx` restored on first idle push (T2-be drift fixer).
10. H2D: extruder tabs appear (dual_nozzle); fixture-only.
11. External spool select: single-nozzle sends ams 255/tray 0 (wire 254); dual sends tray 0/1 (wire 254/255). Verify on H2D fixture.
