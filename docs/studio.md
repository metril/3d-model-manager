# Bambu Studio sidecar

The real Bambu Studio GUI, streamed to the browser (Selkies over WebSocket, labwc + Xwayland) from a `studio` container, so models can be edited in the full app. The api reverse-proxies `/studio/` behind our login. Opt-in: nothing runs unless the `studio` profile is on.

## Security
The Selkies desktop includes a terminal with passwordless sudo. Only our login protects it. **Never publish ports 3000/3001/3010** (`compose.studio.yaml` uses `expose` only). `TDMM_STUDIO_LAUNCHER_TOKEN` adds a shared secret on the launcher API (`X-Launcher-Token`). Studio sits on its own `studio_net` network shared only with `api`, so the desktop cannot reach db/redis. The Selkies file manager is disabled (`SELKIES_FILE_TRANSFERS=`); `STUDIO_ALLOWED_ORIGINS` restricts WebSocket origins to your public URL. Never give Studio printer credentials (access code, serial, cloud login): it is export-only by design, and the A1 tolerates only 1-2 MQTT clients, which `printerd` already uses.

## Enable
```
# .env
COMPOSE_PROFILES=studio          # comma-join with other profiles (printer,beat,...)
PUID=1000                        # same PUID/PGID for api, worker-io and studio
PGID=1000
```
Dev host (Intel/AMD GPU, `/dev/dri`):
```
docker compose -f compose.yaml -f compose.studio.dri.yaml up -d studio
```
NVIDIA host:
```
docker compose -f compose.yaml -f compose.studio.gpu.yaml up -d studio
```
Production (published image `ghcr.io/metril/3d-model-manager-studio`):
```
docker compose -f compose.yaml -f compose.prod.yaml -f compose.studio.gpu.yaml up -d
```
Without a GPU override Studio renders in software (works, slow).

## NVIDIA host prerequisites
- Driver >= 580 and nvidia-container-toolkit (`runtime: nvidia`).
- Kernel params `nvidia-drm.modeset=1 nvidia_drm.fbdev=1` (reboot), or run `nvidia-modprobe --modeset`.
- A display is not required, but a headless host needs a dummy plug or the modeset step above so `/dev/dri/renderD128` and `/dev/nvidia-modeset` exist.
- Verify with `scripts/studio-gpu-check.sh [render-node] [seconds]`; it prints PASS/FAIL for EGL device, NVENC encoder and no software fallback.

## HTTPS
The stream uses WebCodecs, which needs a secure context: serve the app over HTTPS (or use `localhost`).

## Behaviour
- **First run**: a fresh `/config` (`studio_config` volume) shows a modal Setup Wizard in the stream. Complete it once (pick a printer and filaments); it persists. It cannot be skipped by a config flag.
- **Relaunch semantics**: Studio's native single-instance handoff is broken (D-Bus abort), so opening a file kills the running Studio and starts a new one on that file (~5 s to first window). **Unsaved work in the open session is lost.** Non-Bambu 3MFs raise a "Standard 3mf Import color" dialog to answer in the stream.
- **Launcher API** (`:3010`, internal): `GET /health`, `POST /open {"path": "/exchange/..."}` (202; 400 if outside `/exchange` or missing; 409 while relaunching), `POST /stop`.
- **Exchange folder**: `STUDIO_EXCHANGE_HOST_DIR` (default `./studio-exchange`) is mounted at `/exchange` in api, worker-io and studio. Studio runs as `PUID:PGID` and writes there as that user; run all three with the same PUID/PGID so each can read, delete and chmod what the others wrote. On each open the launcher chowns the session folder `/exchange/<token>` to Studio's user and points Studio's save/export dialogs at it (`/exchange` is the fallback default).
- Memory cap `STUDIO_MEM_LIMIT` (default 4g); the image is ~6 GB on disk.

## Troubleshooting
- **Blank/black stream, "insecure context"**: WebCodecs needs HTTPS (`localhost` is fine). See HTTPS above.
- **Studio shows down in Settings -> Services**: `docker compose logs studio`. The healthcheck is the launcher's `:3010/health` (`start_period` 40s); the first start also pulls a ~6 GB image.
- **"Open in Bambu Studio" returns 409**: from the launcher it means a relaunch is in progress, so retry in a few seconds. A `studio_disabled` 409 from the api means the `studio` profile is not enabled.
- **400 on open**: the launcher rejects paths outside `/exchange`; the api returns 400 for file types Studio cannot open.
- **Saved/exported files do not appear under the model**: PUID/PGID differ between api, worker-io and studio. Compare with `ls -ln studio-exchange/`.
- **Setup Wizard modal on first run**: complete it once in the stream; it persists in `studio_config`.
- **Slow rendering**: no GPU override in use. Add `compose.studio.dri.yaml` (Intel/AMD) or `compose.studio.gpu.yaml` (NVIDIA; verify with `scripts/studio-gpu-check.sh`).
- **arm64 host**: unsupported. The image is x86-64 only (`platform: linux/amd64`).
- **401/403 on `/studio/`**: log in as an admin; the proxy and sessions API are admin-only.
