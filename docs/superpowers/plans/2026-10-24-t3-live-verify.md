# T3 live verify (A1 Mini)
- [ ] Print finishes/fails: plate pill appears (`awaiting_plate_clear`); "clear plate" clears it; with `queue.require_plate_clear` on, queue holds until cleared, then dispatches.
- [ ] Toggle each xcam module the printer reports; value echoes in state; never_halt omits sensitivity.
- [ ] Toggle auto-recovery and auto_switch_filament; state reflects.
- [ ] Configure an AMS-lite slot (material, colour, temps); Studio reflects; reset empties it.
- [ ] Rename AMS unit; label shows in status.
- [ ] Browse SD (`/`, subdirs), download a file, delete a file, storage bar (AVBL/STAT or sdcard fallback).
- [ ] Print a `.3mf` from SD (use_ams on/off); external PrintJob created; then flip `sd_print` verified.
