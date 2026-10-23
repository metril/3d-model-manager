# Q6 live verify (A1 Mini)

Prereqs: stack rebuilt (api, worker, beat, printerd); an ntfy topic you can watch (app or ntfy.sh/<topic>).

1. Settings -> Notifications -> add channel "ntfy", topic set, events job_finished + hms_error + dev_mode_off, "Attach photo" on. Press Test: a "Test notification" push arrives; log shows `sent`.
2. Start a short print on the A1 Mini; let it finish. Expect a "Print finished" ntfy push with the camera photo attached within ~15 s (log row `sent`).
3. Raise an HMS fault (e.g. pause with filament runout / open the AMS lid mid-print). Expect an `hms_error` push with the decoded text (or the raw code if HMS catalog is not merged).
4. Turn Developer Mode off on the printer's screen (LAN mode -> Developer mode). Expect a `dev_mode_off` push once its detection flips to off.
5. Quiet hours: set a window covering now, trigger Test-equivalent event (pause/resume a print); log shows `quiet`, no push.
6. Digest: set `digest_cron` a couple of minutes ahead (UTC, e.g. `35 14 * * *`), trigger events; log shows `queued`, then one digest push and rows turn `digested`.
7. Break the topic server URL; Test shows an error; a real event retries (5 s, 30 s, 120 s) and ends as `failed` in the log.
