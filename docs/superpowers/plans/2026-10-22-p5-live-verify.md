# P5 live-verify (A1 Mini, AMS lite + spools + print dialog)

- [ ] `tray_uuid` for a Bambu RFID spool vs a third-party (non-RFID) spool:
      confirm the real tag reads as 24+ hex chars and a non-RFID/third-party
      spool reports all-zeros (`"0"*32`), which `_all_zero_uuid` in
      `bambu.py` treats as "no tag" and nulls out.
- [ ] `remain` for a non-RFID spool: confirm it comes back `-1` (unknown) on
      the wire, not `0`, so `_tray_entry`'s `remain >= 0` guard is actually
      load-bearing and not just defensive.
- [ ] `ams_mapping` index semantics end-to-end: slice a 2-colour plate whose
      3MF project also references an unused filament id (e.g. filament 2
      unused), send it, and confirm the mapping sent to the printer is
      `filament_id - 1` indexed with value `unit*4 + slot` for a used id and
      `-1` (`UNMAPPED`) for the unused one (`to_ams_mapping` in
      `ams_match.py`).
- [ ] External-spool mapping value on the real A1 Mini: confirm the
      external/vt_tray slot maps to `254` (`EXTERNAL_SLOT` in
      `spool_inventory.py`, unit `255`/`EXTERNAL_UNIT`) in the outgoing
      `ams_mapping`, and that the printer accepts it for a filament mapped
      to the external spool holder.
- [ ] AMS unit `id` on a single AMS lite: confirm the unit's own `id` field
      (not list position) is what `_ams_trays` uses for `ams_unit`, and that
      it's `0` for the sole/first unit as assumed.
- [ ] `vt_tray` appears in the MQTT dump only when an external spool is
      actually loaded/clipped in — confirm it's absent (not an empty dict)
      with nothing loaded, and that the external row disappears from the
      tray list when nothing's clipped in.
- [ ] Raw tray payload keys, captured verbatim from a live dump: `tray_uuid`,
      `remain`, `tray_type`, `tray_color` (8-hex `RRGGBBAA` — confirm `_hex6`
      strips the alpha correctly) for both an AMS slot and the external
      spool.
- [ ] `remain` percent granularity during a real print: is it coarse (e.g.
      steps of 1% or less frequent) such that the `ams_delta` fallback
      (grams inferred from remain% deltas when no 3MF-based estimate exists)
      produces sane, non-jumpy grams for a real print, or does it need
      smoothing/debouncing.
- [ ] PrintDialog end-to-end on the real A1 Mini: run a two-colour print
      through the modal with filament→spool mapping set, let it finish, and
      confirm `weight_used_g` on each mapped spool increases by the grams
      the 3MF plate reports for that filament (and `remaining_g` drops by
      the same amount).
- [ ] RFID spool auto-assign: load an RFID-tagged spool into an AMS slot
      with no prior assignment and confirm printerd auto-assigns it to the
      matching `Spool` row (by `tray_uuid`) without a manual pick in the UI.
- [ ] Run cost after the above print uses the assigned spool's price
      (`cost per kg`), not a default/global filament cost.
