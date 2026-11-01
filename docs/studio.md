# OrcaSlicer sidecar

The real OrcaSlicer (v2.4.2) GUI, streamed to the browser (Selkies over WebSocket, labwc + Xwayland) from a `studio` container, so models can be edited in the full app. The api reverse-proxies `/slicer/` behind our login. Opt-in: nothing runs unless the `studio` profile is on.
Internal identifiers (`studio` service/profile/volume, `/api/studio/*`, `TDMM_STUDIO_*`/`STUDIO_*` env vars) keep the "studio" name for deployment compatibility; the user-facing name is OrcaSlicer and its URL is `/slicer/`.

> **Migrating from Bambu Studio.** The sidecar now runs OrcaSlicer (`lscr.io/linuxserver/orcaslicer`, pinned by digest) instead of Bambu Studio. The `STUDIO_*` / `TDMM_STUDIO_*` settings, ports, volumes and the launcher API are unchanged. The old `studio_config` volume held Bambu Studio's config (`~/.config/BambuStudioBeta`) and is **not migrated**: OrcaSlicer starts with a fresh config and shows its first-run setup wizard once (pick the printer and filaments). Remove the stale volume if you want the space back (`docker volume rm <project>_studio_config`). The headless slicer sidecar (`slicer/`) migrated too: `BAMBU_CLI`/`BAMBU_DIR`/`BAMBU_VERSION` still work as aliases of `SLICER_CLI`/`SLICER_DIR`/`SLICER_VERSION`, and `/health` keeps a deprecated `bambu_version` next to `slicer_name`/`slicer_version`.
>
> **Caveats.** OrcaSlicer's own cloud/LAN "send to printer" for Bambu printers depends on Bambu's network plugin and is unreliable for the A1 mini (upstream issue #13025). That does not affect 3DMM, which prints through its own MQTT/FTPS path: use the sidecar for editing and export only. OrcaSlicer's CLI renders no plate thumbnails (the app renders its own). Developer Mode on the printer is not needed for any of this.

## Security
The Selkies desktop includes a terminal with passwordless sudo. Only our login protects it. **Never publish ports 3000/3001/3010** (`compose.studio.yaml` uses `expose` only). `TDMM_STUDIO_LAUNCHER_TOKEN` adds a shared secret on the launcher API (`X-Launcher-Token`). The slicer sits on its own `studio_net` network shared only with `api`, so the desktop cannot reach db/redis. The Selkies file manager is disabled (`SELKIES_FILE_TRANSFERS=`); `STUDIO_ALLOWED_ORIGINS` restricts WebSocket origins to your public URL. Never give Studio printer credentials (access code, serial, cloud login): it is export-only by design, and the A1 tolerates only 1-2 MQTT clients, which `printerd` already uses.

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
Without a GPU override OrcaSlicer renders in software (works, slow).

## NVIDIA host prerequisites
- Driver >= 580 and nvidia-container-toolkit (`runtime: nvidia`).
- Kernel params `nvidia-drm.modeset=1 nvidia_drm.fbdev=1` (reboot), or run `nvidia-modprobe --modeset`.
- A display is not required, but a headless host needs a dummy plug or the modeset step above so `/dev/dri/renderD128` and `/dev/nvidia-modeset` exist.
- The container toolkit only mounts libraries the host has. A compute-only driver install is not enough: the compositor needs NVIDIA's GBM backend and EGL. On Debian (headless, no desktop needed):
  `apt install --no-install-recommends libnvidia-allocator1 libegl-nvidia0 libnvidia-eglcore libgles-nvidia2 nvidia-egl-icd libnvidia-egl-wayland1 libglx-nvidia0 libnvidia-glcore`
  (GBM + EGL for the compositor and encoder; GLX for OrcaSlicer itself, which runs under Xwayland. Without GLX the stream is GPU-encoded but the model view is llvmpipe.)
- Docker >= 28 routes `--gpus` / `runtime: nvidia` through the toolkit's CDI spec (`/var/run/cdi/nvidia.yaml`), which is regenerated at boot but not after library-only installs. After adding NVIDIA packages run `systemctl restart nvidia-cdi-refresh.service` (or `nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml`), and do not keep a second copy under `/etc/cdi`.
- Verify with `scripts/studio-gpu-check.sh [render-node] [hold-seconds]`; it prints PASS/FAIL for EGL device, NVENC encoder, host libs, CDI spec freshness and no software (Pixman) fallback, with in-container GBM/EGL probes on failure.
- Symptoms map: `CREATE_DUMB ... Permission denied` + Pixman = GBM backend missing (allocator); `Failed to create EGL display` = libEGL_nvidia not mounted (host package or stale CDI spec); checks pass but the model view lags = GLX missing (`glxinfo -B` on `DISPLAY=:0` inside the container shows llvmpipe; want the NVIDIA renderer).

## HTTPS
The stream uses WebCodecs, which needs a secure context: serve the app over HTTPS (or use `localhost`).

## Behaviour
- **First run**: a fresh `/config` (`studio_config` volume) may show OrcaSlicer's setup wizard in the stream. Complete it once (pick a printer and filaments); it persists.
- **Relaunch semantics**: the launcher does not rely on native single-instance handoff, so opening a file kills the running OrcaSlicer and starts a new one on that file (~5 s to first window). **Unsaved work in the open session is lost.**
- **Launcher API** (`:3010`, internal): `GET /health`, `POST /open {"path": "/exchange/..."}` (202; 400 if outside `/exchange` or missing; 409 while relaunching), `POST /stop`.
- **Exchange folder**: `STUDIO_EXCHANGE_HOST_DIR` (default `./studio-exchange`) is mounted at `/exchange` in api, worker-io and studio. Studio runs as `PUID:PGID` and writes there as that user; run all three with the same PUID/PGID so each can read, delete and chmod what the others wrote. On each open the launcher chowns the session folder `/exchange/<token>` to Studio's user and points Studio's save/export dialogs at it (`/exchange` is the fallback default).
- Memory cap `STUDIO_MEM_LIMIT` (default 4g); the image is ~6 GB on disk.

## Troubleshooting
- **Blank/black stream, "insecure context"**: WebCodecs needs HTTPS (`localhost` is fine). See HTTPS above.
- **Studio shows down in Settings -> Services**: `docker compose logs studio`. The healthcheck is the launcher's `:3010/health` (`start_period` 40s); the first start also pulls a ~6 GB image.
- **"Open in OrcaSlicer" returns 409**: from the launcher it means a relaunch is in progress, so retry in a few seconds. A `studio_disabled` 409 from the api means the `studio` profile is not enabled.
- **400 on open**: the launcher rejects paths outside `/exchange`; the api returns 400 for file types OrcaSlicer cannot open.
- **Saved/exported files do not appear under the model**: PUID/PGID differ between api, worker-io and studio. Compare with `ls -ln studio-exchange/`.
- **Setup wizard on first run**: complete it once in the stream; it persists in `studio_config`.
- **Slow rendering**: no GPU override in use. Add `compose.studio.dri.yaml` (Intel/AMD) or `compose.studio.gpu.yaml` (NVIDIA; verify with `scripts/studio-gpu-check.sh`).
- **arm64 host**: unsupported. The image is x86-64 only (`platform: linux/amd64`).
- **401/403 on `/slicer/`**: log in as an admin; the proxy and sessions API are admin-only.
