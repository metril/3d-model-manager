# Q4-web live verify (A1 Mini)

Prereq: backend PR #21 merged and deployed; compose rebuilt.

- [ ] Printer card / detail tile streams live; hover toolbar appears.
- [ ] Zoom: wheel, +/- buttons, pinch on a touch device; drag pans while zoomed; double-click resets.
- [ ] Settings -> Printers -> Camera: set rotation 90 -> tile rotates and fits without cropping; set back to 0.
- [ ] Fullscreen button enters/exits (Esc exits and button state follows).
- [ ] Light button (control user only; hidden for print-only user) toggles the chamber light (needs Q2 `control/light`).
- [ ] Mode toggle: snapshot mode refreshes a still every ~2 s; fps selector changes stream rate.
- [ ] Stall: block camera traffic (or stop printerd camera) for >5 s -> tile reconnects with backoff; after 5 tries shows offline, recovers after 10 s once restored.
- [ ] "Open in window" -> `/camera/{id}` popup with status bar; closing it stops the stream (printerd idles).
- [ ] Settings -> Camera -> Create overlay token; copy URL; open it in a private window (no login): transparent bg, status strip updates every 5 s; revoke -> overlay shows "Overlay unavailable" within 5 s.
- [ ] `/camwall` with max live 1: one tile streams, others refresh snapshots every N s; click a still to promote it; settings persist after reload.
- [ ] Beta badge appears only on an RTSP-source printer.
