# T4-web live verify (A1 Mini + real backend with T4-be)

1. Print dialog: Send step shows a collapsed "Scheduling" row ("ASAP"). Add to queue untouched -> POST /queue body has no scheduling keys.
2. Expand, set Start at +5 min, Add to queue: row shows "Scheduled <local time>" chip; cancel icon clears it (back to ASAP); entry dispatches at the time.
3. Manual start + Add to queue: row shows "Manual start" + Start (queue:review only); printer idles until Start is pressed.
4. Require previous success: fail print #1 (cancel on printer is neutral), print #2 turns Skipped ("previous print failed"); lane header and row show Resume; Resume re-queues it.
5. Preheat On with target: row shows Preheating/Soaking spinner before send (A1 Mini: bed soak label "Bed soak target"; no chamber).
6. Power off after: switch hidden for a printer without a plug; with a plug, the plug turns off after the last queued print; row shows "Power off after" badge.
7. Pencil icon on a waiting row edits scheduling via PATCH /queue/{id}.
8. Queue -> Batches tab: create batch (model, qty 3, plate targets), Dispatch -> 3 entries with Batch badge linking back (`?tab=batches&batch=ID` opens the drawer); Ungroup clears badges; Delete cancels, second Delete removes.
9. Settings -> Printer -> Queue preheat: edit, Save, reload persists; out-of-range values blocked inline.
