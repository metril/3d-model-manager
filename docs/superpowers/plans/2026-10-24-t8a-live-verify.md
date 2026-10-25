# T8a live verification (Apprise)

1. Create a Slack incoming webhook (`https://hooks.slack.com/services/T/B/X`); the Apprise URL is `slack://T/B/X/#channel`.
2. Settings > Notifications > Add channel > "Apprise (Slack, Discord, ...)". Paste the URL into Apprise URLs, enable "Print finished" and "Attach camera photo", save.
3. Edit the channel: the URLs textarea is empty with a "leave blank to keep" placeholder (secret kept); saving without changes preserves the URL.
4. Click Test: the Slack channel receives the test message.
5. Finish a print on the A1 Mini with camera on: Slack receives the finish message with the photo attached.
6. Break the URL (add a bad line): Test reports "invalid Apprise URL on line N" with no URL text; NotificationLog shows only `scheme://host`.

## Email prefs
1. With SMTP configured (Server tab), open Settings > General: the "Email notifications" card appears (absent when SMTP is unset). A user without an email address sees the hint.
2. Tick "Print finished", start a print as that user (with an email on the account), let it finish: one finish email arrives, with the same subject/body as the channel template.
3. Another user's prefs do not trigger mail for this job; prints started by the printer itself (external) send nothing.
