# P2 live-verify (A1 Mini)

- [ ] LAN send from the app: confirm `/cache/tdmm-<job_id>.gcode.3mf` exists after start (FTPS `LIST /cache`).
- [ ] Studio "Send" (not "Print"): record the stored name/extension (`/cache/<subtask>.3mf` vs `.gcode.3mf`, root vs `/cache`); fix `services/archive.py` candidates if different.
- [ ] Touchscreen reprint from SD: subtask_name reported and file location.
- [ ] `/cache` purge: is the file still present after the run finishes / after the next print / after reboot?
- [ ] Cancel from touchscreen and from Studio at ~20%: record `gcode_state` and `print_error`; confirm `CANCEL_ERROR_CODES` in `printerd.py`.
- [ ] Stop from the app: run result `stopped`, job `canceled`, partial grams ≈ est × layer/total.
- [ ] Completed small print: archive row has 3MF, thumbnail, grams, cost; run shows in Printer → history.
- [ ] printerd restart mid-print: no duplicate external job; archive still created on finish.

## Additional items (accumulated)

- [ ] FTPS `LIST` line format on the A1 Mini (10.10.76.70): confirm the parser handles it; path is `/cache` vs `cache`; vendored client returns None (not raises) on failure.
- [ ] `CANCEL_ERROR_CODES = {50348044}` is an assumption: verify against a real cancel.
- [ ] 3MF location after an app LAN send (`/cache/tdmm-{job_id}.gcode.3mf`) vs a Bambu Studio send (`<subtask>.gcode.3mf` in `/cache` or `/`).
- [ ] `gcode_state` value on cancel vs stop (FAILED vs IDLE/FINISH).
- [ ] Thumbnail extraction from a real printer-side 3MF.
