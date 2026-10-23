# Q3 live-verify (A1 Mini)

Run against the real printer after the web image is rebuilt (`docker compose build web && docker compose up -d`).

- [ ] **Visual match**: `/` shows the bambuddy layout at M: model image (A1 mini PNG), name link, `0.4 mm` nozzle + hours, badge row (Connected, `-NNdBm`, FW, queue count), status block, temp/fan tiles, controls, AMS unit card(s), footer. No chamber, aux fan, chamber fan, airduct or door controls appear.
- [ ] **Sizes**: S/M/L/XL selector persists after reload (`printerCardSize`); grid columns 5/3/2/1; S card click expands to M, scrolls into view, "Back" returns.
- [ ] **Fan popover**: click Part fan, press 50% -> tile reads 50% and the icon spins; the physical fan runs; status confirms within ~3 s; Off stops it.
- [ ] **Bed target**: click Bed, press PLA 60 -> tile shows `current -> 60` immediately; printer screen shows target 60; Off returns to 0. Set an unreachable value with the printer in Dev Mode off: tile reverts after 10 s and a toast explains.
- [ ] **Light**: toggle -> chamber light switches; state matches after the next status poll.
- [ ] **Speed**: during a print press Sport; the printer UI shows Sport.
- [ ] **Movement** (idle): step 10, Z+ moves the bed; Home homes; Extrude with a cold nozzle shows the "Heat the nozzle to at least 170" toast. All disabled while printing.
- [ ] **Skip objects**: start a multi-object plate from the app; the status block shows the skip button with the object count; skip one object in the dialog; the printer skips it and the count drops.
- [ ] **Pause/Resume/Stop** each ask for confirmation first.
- [ ] **Dashboard**: summary bar counts match; status chip filters and `?status=`, `?sort=eta|status|model`, hide-offline, search all round-trip through the URL; Select -> pause/stop selected sends one request per printer.
- [ ] **Drop**: drag a `.3mf` onto a card -> toast "Queued ..." and the entry appears in the queue targeting that printer.
- [ ] **Banner**: turn Developer LAN mode off on the printer (or use an A1 with it off); within ~60 s a banner "Developer LAN mode is not enabled on: <name>" appears with the wiki link; dismissing hides it for the session. Before the dev-mode probe resolves, an online printer with unknown state shows the grey variant.
- [ ] **Detail page** `/printers/<id>`: XL card with the large camera, up-next, run history and stats.
