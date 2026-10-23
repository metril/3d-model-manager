# Q7-web live verification (A1 Mini + real stack; needs PR #24 merged and deployed)

- [ ] Printer detail shows Maintenance card; add "Lube rods" (100 h); due status/remaining hours render; set a 1-day interval and confirm "soon/overdue" highlight after the interval passes.
- [ ] Mark done with a note: remaining resets, History lists the note and hours.
- [ ] Edit and delete a task; viewer without printers:control sees no mutate buttons.
- [ ] Maintenance mode on: queue a job, confirm the dispatcher does not start it on this printer; off again, it dispatches.
- [ ] Settings > Printer > printer editor > Smart plug: configure Tasmota/HA/MQTT/REST plug; bad config shows the server 422; secrets show `***` after reload and survive re-save.
- [ ] PlugRow on detail page: state, watts, toggle on/off physically switches the plug; non-control user sees disabled switch.
- [ ] Auto-off: finish a print, plug turns off after the configured minutes (nozzle < 50 C).
- [ ] Settings > Storage > Backups: set interval + git provider/token; "Run backup now" -> run listed ok with size/location; backup appears in storage and git repo; token field blank after save.
- [ ] Restore settings (admin): confirm dialog, success count; non-admin sees no run list/buttons.
- [ ] Settings > General > Updates: current vs latest, release link; UpdateBadge appears in top bar only when an update exists.
- [ ] Support bundle downloads a zip with redacted settings (admin only).
