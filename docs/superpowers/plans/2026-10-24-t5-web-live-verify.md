# T5-web live verification

Run against a stack with T5-be merged (and Home Assistant configured for the sensor items).

- [ ] Spools: tick a row, header checkbox goes tri-state/select-all; sticky toolbar appears with count.
- [ ] Bulk edit: tick only Cost / kg, apply; only that field changes on all selected spools. Force a failure (invalid hex) and confirm the per-id error list shows and the dialog stays open.
- [ ] Archive / Restore (toggle "Show archived") and Delete (count in confirm dialog) on a multi-selection.
- [ ] Export CSV downloads `spools.csv`; Import CSV with that file: dry-run shows all rows as update, Commit applies; edit a row and add a bad one: error row shown, nothing written until Commit.
- [ ] Wait >15 min after a dry-run, Commit: expired message + Re-check works.
- [ ] Labels: select spools, open Labels; preview renders for tight/roomy x A4/Letter/roll 62mm; Download PDF and PNG; scan a QR and confirm it opens the spool.
- [ ] Spool form: location combobox finds existing, "Create ‘…’" makes + selects one; table Location column/filter reflects it.
- [ ] Spool form: add two suppliers, set the purchase source, save, reopen: values persisted.
- [ ] Spool form: colour catalog search (e.g. "jade") fills hex, name and empty brand.
- [ ] `/inventory/spools?tab=suppliers|locations|colors` deep links open the right tab; Suppliers CRUD; Locations CRUD (deleting unassigns spools).
- [ ] Locations: Sensors -> Add sensor lists real HA entities; thresholds + show-on-card/notify saved; reading updates within ~30 s; crossing a threshold shows the Alert badge and fires the notification.
- [ ] Colour catalog: filter, add/edit/delete, Reset defaults (confirm) restores seed data.
