# T8b live verify

1. History -> open a sliced run with a stored `.gcode.3mf` -> "Show toolpath": viewer loads lazily (network: `/api/archives/{id}/gcode/plates`, then `/gcode?plate=N`, `Content-Encoding: gzip`).
2. Multi-plate archive: plate selector appears; switching reloads the toolpath.
3. Run without a stored file / without plate G-code: no button / "No G-code toolpath available".
4. Other user without access to the printer: both `/gcode` routes return 404.
5. `curl -b cookie 'http://host/api/printers/ID/files/gcode?path=/sdcard/x.gcode.3mf'` on a real A1 Mini: streams the plate G-code; `..`, relative or non-3mf paths give 422. Temp dir `/tmp/tdmm-gcode-*` is removed afterwards.
6. Large (100+ MB) G-code: server RSS stays flat while streaming.
