# T3-web live verification (A1 Mini)

- [ ] Finish a print: card shows "Plate needs clearing"; "Mark plate cleared" clears the pill and toasts. With `require_plate_clear` on, the queue holds until cleared.
- [ ] Footer "Print options": rows match the modules the printer reports; toggling each module and changing sensitivity (incl. Never halt) is echoed back by the printer; a rejected command reverts the switch.
- [ ] Auto-recovery and "AMS backup (auto switch filament)" toggle and persist after reopening.
- [ ] Slot menu -> "Configure slot…": set material/colour/temps with "Apply to printer"; AMS display on the printer and Studio match. "Reset slot" empties it.
- [ ] AMS unit menu -> "Rename AMS…": label shown instead of "AMS n"; clearing the label restores it.
- [ ] Footer "Files": browse folders via breadcrumb, storage bar matches the SD card, Download saves the file, Delete removes it after confirm.
- [ ] Files -> Print on a `.3mf`: confirm, print starts, card shows the job. No Print button on non-3mf files.
- [ ] View-only user: no Print options button, no slot/AMS menus, no Delete/Print in Files.
