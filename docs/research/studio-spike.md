# Bambu Studio sidecar spike (S0)

Date 2026-10-31. S0a ran on the dev host (Intel HD 630 + Radeon RX Vega M, no NVIDIA). S0b is `scripts/studio-gpu-check.sh`, to be run by the user on the NVIDIA host (not run here). Raw captures: `docs/research/studio-spike/capture.txt`.

## Result in one line
Launcher mode is **relaunch**. Native single-instance handoff is broken in the shipped Studio (D-Bus abort). Relaunch with the file as argv takes about 4.5 s to the first window.

## Image
- `lscr.io/linuxserver/bambustudio:latest` = `sha256:857e937fe1c3451244cb178952e0e7a00241ab48c32cb1ce9ea6b27e4fd729e5` (Selkies 2.1.0, pixelflux 2.2.1, nginx 1.28.3, labwc + Xwayland).
- Size: 6.21 GB on disk, 1.62 GB content; container writable layer 36 MB after use.
- Default mode is **Wayland** (`PIXELFLUX_WAYLAND=true`): Selkies captures its own compositor `wayland-1`; Studio runs on the app compositor labwc `wayland-0` through Xwayland `:0`. No Xvfb.

## Header capture
| URL | Headers |
|---|---|
| `GET /studio/` (static HTML, nginx) | none of CSP / X-Frame-Options |
| `GET /studio/api/status` (Selkies) | `Content-Security-Policy: frame-ancestors 'self'` |
| same with `SELKIES_FRAME_ANCESTORS='self' http://localhost:9090` | `frame-ancestors 'self' http://localhost:9090` (confirmed after restart) |
| WS handshake `/studio/api/websockets` | `101`, `Sec-WebSocket-Protocol: selkies`, same CSP |

The HTML shell carries no frame protection from nginx, so S2 must add its own `frame-ancestors`/`X-Frame-Options` on the proxied HTML. The CSP above only covers API and WS responses. WebCodecs needs a secure context (https or localhost); not tested, documented only.

## Processes and env
Process tree: s6 supervises `svc-nginx` (:3000 plain, :3001 TLS with `/config/ssl/cert.pem`), `svc-selkies` (python `selkies --addr=localhost`, aiohttp on 127.0.0.1:8082), `svc-de` (`startwm_wayland.sh` -> labwc -> `/config/.config/labwc/autostart` -> `/opt/bambustudio/AppRun`). Studio is a child of the labwc autostart script, not of s6, so killing it does not respawn it.

Env Studio runs with (read as `abc`; root cannot read `/proc/<pid>/environ`):

| Var | Value |
|---|---|
| HOME | `/config` |
| DISPLAY | `:0` |
| WAYLAND_DISPLAY | `wayland-0` |
| XDG_RUNTIME_DIR | `/config/.XDG` |
| LD_LIBRARY_PATH | `/opt/bambustudio/bin` |
| LC_ALL | `C` (set by AppRun) |
| PIXELFLUX_WAYLAND / DRINODE / DRI_NODE | `true` / render node |

`bambu-studio` is not on PATH. The binary is `/opt/bambustudio/bin/bambu-studio`. `DBUS_SESSION_BUS_ADDRESS` is unset (autolaunch). Studio config lives in `/config/.config/BambuStudioBeta/` (not `BambuStudio/`), file `BambuStudio.conf` is plain JSON; logs there are encrypted (`*_enc_dc.log`) so Studio's own log is useless for observation.

## WebSocket and HTTP endpoints (SUBFOLDER=/studio/)
nginx maps `/studio/` to the static web client and `/studio/api` to Selkies (127.0.0.1:8082) with Upgrade, `proxy_buffering off`, `proxy_read_timeout 3600s`, `client_max_body_size 0`. Also `/studio/pelorus/` -> :5100 and `/devmode` -> :5173 (dev only, ignore).

Selkies routes (`stream_server.py`), all under `/studio`:
- WS: `/studio/api/websockets` (used here), `/studio/api/ws`, `/studio/api/webrtc/signaling` (WebRTC mode, off by default).
- HTTP: `/studio/api/status`, `/studio/api/health` (no auth), `/studio/api/tokens`, `/studio/api/switch`, `/studio/api/recording`, `/studio/api/sessions`, `/studio/api/files/` (file manager upload/download), `/studio/api/print/`, `/studio/api/apps/*`.
- Subprotocol: `selkies`; with secure mode the client adds `selkies.token.<base64url>`. Server enforces Origin (`SELKIES_ALLOWED_ORIGINS`, default `*` in this image) on WS upgrade.
- Proxy implication: forward the `Sec-WebSocket-Protocol` header and select `selkies`; pass Origin untouched or set `SELKIES_ALLOWED_ORIGINS` to our origin.
- Port 3001 (TLS, self-signed) mirrors 3000; we only need 3000.
- `/studio/api/files/` is a second way to move files in and out of the container (upload/download). Disable via `SELKIES_FILE_TRANSFERS=` unless wanted, since we use the exchange folder.

## Single-instance handoff
Tested with Studio running and a 3MF as argument:
1. Default (`app.single_instance=false`): the second `bambu-studio /config/a.3mf` starts a **second full process and window**. The running one is unchanged.
2. `"single_instance": true` (key is `app.single_instance`, conf only had `false`): the second process **aborts with rc=134**: `dbus_message_new_method_call ... assertion "_dbus_check_is_valid_path (path)" failed`. Root cause in upstream source `src/slic3r/GUI/InstanceCheck.cpp:242`: the object path `/com.bambulab/BambuStudio/InstanceCheck/Object<hash>` contains a `.`, invalid for D-Bus, so the message is never sent and nothing opens in the running instance.

Verdict: **no usable handoff; use relaunch.** The pref gives nothing, leave it `false`.

### Relaunch (works)
Launcher steps, as `abc`:
```
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 HOME=/config XDG_RUNTIME_DIR=/config/.XDG \
       LD_LIBRARY_PATH=/opt/bambustudio/bin LC_ALL=C
pkill -f '^/opt/bambustudio/bin/bambu-studio'          # wait until gone; -9 after ~10 s
nohup /opt/bambustudio/bin/bambu-studio /exchange/<token>/in/<file>.3mf >/dev/null 2>&1 &
```
Pitfalls found: `pkill -x bambu-studio` matches nothing (comm is `bambustu_main`), use `-f` with an anchored pattern, and never run `pkill -f bambu-studio` from a shell whose own cmdline contains it. Do not call `AppRun` through `docker exec` as root.

Timing (dev host): SIGTERM to process gone about 0.02 s; relaunch to first window ("Loading...") **4.5 s**; project title ("b") visible within about 12 s. Unsaved work in the running Studio is lost on relaunch, so the API/UI must warn before opening another file when a session is already open.

How a second file is treated: replaces the first (no prompt, because the old process is killed). A non-Bambu 3MF (`a.3mf`, the plain `DotC parts - Part 2.3mf`) raises a modal **"Standard 3mf Import color"** dialog the user must answer in the iframe; a Bambu-authored 3MF (`b.3mf`) opens straight to the project.

### First-run wizard
A fresh `/config` shows a modal **Setup Wizard** (`config_wizard_startup` runs while only default printers exist). Seeding `firstguide.finish=true` did not suppress it. The wizard persists once the user picks a printer/filaments, and `/config` is a volume, so S1 should document one-time completion (or seed a user printer preset) rather than rely on a conf flag.

## Exchange folder ownership
`PUID`/`PGID` make `abc` that uid/gid (verified: `uid=751001104(abc) gid=751000513(abc)`). Files and dirs Studio creates in `/exchange` are owned by that uid:gid on the host, mode 644/755 (umask 022). So the api and worker-io containers (see `compose.yaml` `PUID`/`PGID`, entrypoint drops privileges to them) must run with the **same PUID/PGID** as `studio` (or as root, the default when unset) to read, delete and `chmod` what Studio wrote. The per-token directories the API creates must be writable by that uid, since Studio writes into them as `abc`. Set the same two vars in `.env` for all three services.

## Resources (dev host, Radeon, no client attached)
- `docker stats --no-stream`: idle about 960 MiB RSS, 1-2 % CPU, 335-364 pids (with and without a model loaded; no streaming client attached, so GPU encode cost not measured).
- Image 6.21 GB on disk (1.62 GB compressed content). Plan `STUDIO_MEM_LIMIT` with real headroom (large plates, slicing in the GUI): start at 4 GiB.
- Encoder here: `[pixelflux] Render node 1 encodes H264, H265 on vaapi`, `[Wayland] Renderer: GL on /dev/dri/renderD129`, Selkies encoder `h264enc`.

## S0b: NVIDIA host
`scripts/studio-gpu-check.sh [render-node] [seconds]` prints kernel cmdline and `nvidia_drm modeset`, `/dev/dri` and `/dev/nvidia-modeset`, host `nvidia-smi`, `docker run --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`, then runs the stock image with the NVIDIA stanza and greps its log for the lines identified above, ending in PASS/FAIL lines. It was dry-run here (no NVIDIA): the failure paths print FAIL and exit cleanly. Patterns it asserts:
- `\[Wayland\] Renderer: .* on /dev/dri/` present (EGL/GL device chosen)
- `\[pixelflux\] Render node .* encodes` mentions `nvenc|nvidia|cuda` (else FAIL: vaapi/software fallback)
- no `llvmpipe|softpipe|swrast`

Unknown until run on NVIDIA: the exact encoder token pixelflux prints for NVENC. If the line says something else but the 3D view is smooth, adjust the pattern.

## Implications for S1-S3
1. Launcher = relaunch only. `POST /open` kills Studio, waits, starts `/opt/bambustudio/bin/bambu-studio <file>` as `abc` with the env table; guard with a "session already open" warning (unsaved work lost). Drop the "seed single-instance pref" idea.
2. Launcher runs inside the image as an s6 service (root) and must use `s6-setuidgid abc` plus the explicit env above; it cannot read Studio's environ. Do not rely on `bambu-studio` in PATH.
3. Compose: nothing published; api proxies `/studio/` HTTP and `/studio/api/websockets` WS (subprotocol `selkies`, Origin handling). The proxy must add its own frame protection on the HTML shell. Keep `SUBFOLDER=/studio/` identical on both sides.
4. Same PUID/PGID on studio, api and worker-io; per-token exchange dirs created writable for that uid.
5. First run shows a Setup Wizard and non-Bambu 3MFs prompt "Standard 3mf Import color"; both are answered in the iframe, so document them in `docs/studio.md` and consider handing the user's own file as Bambu 3MF where possible.
6. Disable Selkies' own file manager if not needed (`/studio/api/files/` accepts uploads), and set `SELKIES_ALLOWED_ORIGINS` to our origin once proxied.
7. Studio logs are encrypted; health for S3 should be "process exists + window title" (`xdotool search --name`), or just process + Selkies `/studio/api/health`. Window title becomes the project/file stem ("b") once a project loads.
8. Compose override for dev: `/dev/dri` plus `DRINODE` for the dev host's Radeon; `compose.studio.gpu.yaml` for NVIDIA once S0b passes.
