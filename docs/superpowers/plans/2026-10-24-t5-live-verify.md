# T5 spool housekeeping - live verification checklist

Needs: a running stack on the new image (`docker compose build && up`; migration `d1e5a7c3b9f2` runs at start), HA configured under Settings for the sensor items.

- [ ] Upgrade on a real DB: distinct free-text spool locations became `storage_locations` (case/whitespace deduped); every spool kept its location; `GET /api/spools` still returns `location` (name).
- [ ] Colour catalog seeded (`GET /api/color-catalog` ~638 default rows); `POST /api/color-catalog/reset` as admin restores defaults and keeps custom rows.
- [ ] Suppliers: create/edit/delete; assign two to a spool via `PUT /api/spools/{id}/suppliers` with one purchase source; shows on spool detail.
- [ ] Bulk: select several spools, bulk-update a location + brand, archive, restore, delete (spool with usage history refused per id).
- [ ] CSV: export `spools.csv`, open in a spreadsheet, edit a weight and add a row, import with dry run (diff correct, nothing written), commit; re-commit of the same token returns 410; wait 15 min and confirm expiry.
- [ ] Labels: print a PDF sheet (A4 and Letter, tight and roomy) and a 62 mm roll; scan the QR with a phone -> opens `{public_url}/inventory/spools?spool={id}`; PNG export (zip when >1 page).
- [ ] HA sensors: `GET /api/storage-locations/sensors/entities` lists real entities; attach a humidity sensor to a location with `alert_above`, `notify_on_alert`; within ~2 min readings update; push the humidity over the threshold (breathe on it) and receive exactly one `location_sensor_alert` notification; recovery then re-crossing notifies again.
- [ ] Viewer account: can read all lists, gets 403 on every write.
