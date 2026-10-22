# P6 live-verify (slicer sidecar, profiles/recipes, dispatch -- A1 Mini)

Everything below needs a live `slicer` sidecar container and/or a real A1
Mini; none of it is exercised by the automated suite (the sidecar's own
HTTP surface was unverified while writing `services/slicer_client.py`, and
the dispatcher's multi-printer behavior needs more than one real printer
on the LAN).

- [ ] Pull the pinned image (`docker compose --profile slicer up -d`,
      `ghcr.io/maziggy/bambu-studio-api:bambuddy-1.2.5.7`) and confirm
      `GET /api/slicer/health` reports `ok: true` with a non-empty
      `version` -- `_parse_health` in `slicer_client.py` tries a couple of
      plausible key names (`version`/`app_version`); confirm one of them
      actually matches the real payload shape, and fix `_parse_health`
      (only that function) if not.
- [ ] Compare the sidecar's real endpoints/request/response shapes against
      `_ENDPOINTS` (`health: /health`, `slice: /slice`,
      `profiles: /profiles`) in `slicer_client.py` -- paths, HTTP methods,
      multipart field names for `/slice`, and the response content-type for
      the sliced output. Fix only the mapping layer (`_ENDPOINTS`,
      `_build_slice_request`, `_parse_health`); `SlicerClient`'s public
      contract (`health`/`profiles`/`slice`) must not change.
- [ ] Import a profile bundle from the user's actual Bambu Studio install
      path (`~/.config/BambuStudio/system/` on Linux, or wherever
      **Help -> Show Configuration Folder** points on the machine in hand)
      via `/inventory/profiles`'s Import dialog, and confirm the `BBL.json`
      manifest + `inherits:` chain resolution produces the same flattened
      profile `data` Bambu Studio itself would slice with.
- [ ] A recipe slice of an A1 Mini model actually prints: confirm the
      **plate index** the sidecar sliced matches what gets sent, and that
      the md5 of the `.gcode.3mf` the printer *received* (FTPS) equals the
      md5 of the stored blob the dispatcher created as a new `File` on the
      source revision.
- [ ] Bed-type strings: confirm the sidecar CLI's accepted `bed_type`
      values for a recipe line up with the real BBL `curr_bed_type` enum
      (e.g. `textured_plate`, `cool_plate`, `engineering_plate`,
      `high_temp_plate`) -- a recipe saved with a label the CLI silently
      ignores/defaults on would slice onto the wrong plate.
- [ ] MQTT `nozzle_diameter`: confirm the A1 Mini actually reports this key
      in its push-all dump (`read_state_sync` in `printer_state.py`) --
      `dispatcher.py`'s compatibility check (`NOZZLE_TOL_MM = 0.05`) is
      skipped entirely, not failed, when it's `None`, so a missing key
      silently disables that half of the compatibility check rather than
      erroring.
- [ ] Spread a `spread`/`first_n` recipe's copies across 2 real printers (or
      1 real printer + one left disabled, to exercise the "ineligible"
      branch of `select_printers`): confirm one copy lands on each eligible
      idle printer, and that when one **FINISH**es, the next `waiting`/
      `ready` item targeting it is picked up automatically on the dispatcher's
      next 10s tick with no manual retry needed.
- [ ] `docker compose up` **without** `--profile slicer`: confirm every
      other service still starts and the app stays fully usable --
      `GET /api/slicer/health` should report `configured: false` and
      recipe actions in **Files -> Print** should disable with the
      "Slicer sidecar unavailable" tooltip, with no other feature affected.
