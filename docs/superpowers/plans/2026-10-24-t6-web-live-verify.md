# T6-web live verification (needs T6-be deployed + a real SMTP server)

1. Server tab -> Outgoing email: save host/port/security/credentials; password field shows "***" afterwards. "Send test email" to a real inbox: ok/error shown.
2. General -> Two-factor: Set up authenticator app; scan the QR with a real authenticator (verifies the in-house QR encoder on a physical phone), enter the code, confirm 10 backup codes; Copy and Download .txt work.
3. Enable email codes (needs an email on the account + SMTP): code arrives, confirm turns it on; Disable with password and with an emailed code.
4. Sign out/in: login shows the code step; try totp, email (Send code, 4th request in 10 min shows the rate-limit copy), backup code (consumed once), wrong code, wait >5 min or 5 bad codes -> returned to credentials with "expired".
5. SSO user with 2FA: OIDC callback lands on /login?step=2fa and the token disappears from the address bar; with "Trust identity provider MFA" on, the 2FA step is skipped.
6. Access tab: turn on "Require two-factor for all users" as admin; a user without 2FA gets the blocking enrolment page after login (only log out + setup); completing setup and Continue unlocks the app. Open a second tab mid-session: a 403 refreshes the gate without looping.
7. Users -> Reset 2FA on another user (confirm dialog); they can sign in with password only again (or are forced to re-enrol).
