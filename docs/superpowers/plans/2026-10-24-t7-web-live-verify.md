# T7-web live-verify checklist

Needs the T7-be backend (migrated) and a finished archived run with a timelapse.

1. History -> Calendar: month grid shows counts + hours, colours by ok/failed; prev/next change month; click a day -> Runs tab filtered to that day with a "clear" chip.
2. Runs: tick 2-5 rows (6th is blocked) -> Compare: columns, only differing settings highlighted, correlation rows with n. Same on the dashboard Recent runs card.
3. Run detail: edit notes + tags, Save; reload persists. Admin/print user: set result override, then Reset. Admin: Delete -> confirm -> sheet closes, row gone.
4. Verdict: with confirm enabled (Settings -> Printer -> Outcome confirmation), finish a print: the card shows Good/Bad; after answering it shows verdict + source.
5. PostPrintConfirmDialog: appears for your own pending run anywhere in the app; "Later" hides it for the session (sessionStorage `archive-confirm-snooze`); Good/Bad records source `dialog`. Other users' runs never prompt you.
6. Photos: upload jpeg/png/webp (<=15 MB; gif/oversize rejected client-side), open lightbox, delete with confirm.
7. Edit timelapse: trim handles, speed, save mode replace/new, Process -> toast on done, player refreshes (`new` appears under photos list as kind timelapse).
8. Settings -> Storage -> Archive purge: save, Preview (count + size), Purge now (confirm shows count), last-run stats update.
