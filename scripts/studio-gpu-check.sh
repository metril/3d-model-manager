#!/usr/bin/env bash
# shellcheck disable=SC2015  # pass/fail helpers always return 0
# NVIDIA host check for the Bambu Studio (Selkies) sidecar. Run on the headless
# NVIDIA host, then paste ALL output back. Needs only docker, curl, grep.
#   usage: scripts/studio-gpu-check.sh [render-node] [seconds]
#   e.g.   scripts/studio-gpu-check.sh /dev/dri/renderD128 120
set -euo pipefail

NODE="${1:-/dev/dri/renderD128}"
SECS="${2:-120}"
IMAGE="${STUDIO_IMAGE:-lscr.io/linuxserver/bambustudio:latest}"
CUDA_IMAGE="nvidia/cuda:12.4.1-base-ubuntu22.04"
NAME="tdmm-studio-gpucheck"
PORT="${STUDIO_CHECK_PORT:-3000}"
LOG="$(mktemp)"
FAILS=0
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -f "$LOG"; }
trap cleanup EXIT

hdr() { printf '\n== %s ==\n' "$1"; }
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

hdr "host"
uname -r
hdr "kernel cmdline (want nvidia-drm.modeset=1 nvidia_drm.fbdev=1)"
cat /proc/cmdline
hdr "nvidia_drm modeset (want Y)"
MODESET="$(cat /sys/module/nvidia_drm/parameters/modeset 2>/dev/null || echo missing)"
echo "$MODESET"
[ "$MODESET" = "Y" ] && pass "nvidia_drm.modeset=Y" || fail "nvidia_drm.modeset is '$MODESET' (add nvidia-drm.modeset=1 to the kernel cmdline and reboot)"
hdr "fbdev (informational)"
cat /sys/module/nvidia_drm/parameters/fbdev 2>/dev/null || echo "n/a"

hdr "device nodes"
ls -l /dev/dri/ 2>&1 || true
ls -l /dev/nvidia-modeset /dev/nvidiactl /dev/nvidia0 2>&1 || true
[ -e "$NODE" ] && pass "$NODE exists" || fail "$NODE missing"
[ -e /dev/nvidia-modeset ] && pass "/dev/nvidia-modeset exists" || fail "/dev/nvidia-modeset missing (try: sudo nvidia-modprobe --modeset)"

hdr "host nvidia-smi"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi && pass "host nvidia-smi" || fail "host nvidia-smi"
else
  fail "nvidia-smi not found on host"
fi

hdr "docker --gpus all nvidia-smi"
if docker run --rm --gpus all "$CUDA_IMAGE" nvidia-smi; then
  pass "container nvidia-smi"
else
  fail "docker --gpus all nvidia-smi (is nvidia-container-toolkit configured? nvidia-ctk runtime configure --runtime=docker)"
fi

hdr "Studio container (${SECS}s, 127.0.0.1:${PORT} only)"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  --runtime nvidia --gpus all \
  --device /dev/dri --device /dev/nvidia-modeset \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e PIXELFLUX_WAYLAND=true \
  -e DRINODE="$NODE" -e DRI_NODE="$NODE" \
  -e SUBFOLDER=/studio/ -e TZ=UTC \
  -p "127.0.0.1:${PORT}:3000" --shm-size=1g \
  "$IMAGE" >/dev/null || { fail "could not start the Studio container with the NVIDIA stanza"; echo "RESULT: ${FAILS} FAIL line(s) above"; echo "Paste this whole output back."; exit 1; }
echo "image: $(docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || echo "$IMAGE")"

HTTP="000"
for _ in $(seq 1 "$SECS"); do
  HTTP="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/studio/" || true)"
  [ "$HTTP" = "200" ] && break
  sleep 1
done
echo "GET /studio/ -> $HTTP"
[ "$HTTP" = "200" ] && pass "web UI answers 200" || fail "web UI did not answer 200"

# Let Studio and the capture pipeline settle for the remainder of the window.
sleep 20
docker logs "$NAME" >"$LOG" 2>&1 || true

hdr "encoder / EGL lines from the Selkies log"
grep -E '\[pixelflux\]|\[Wayland\] Renderer|INFO:main:Selkies|nvenc|NVENC|nvidia|EGL|egl' "$LOG" | cut -c1-240 || true

hdr "full verdict"
if grep -qE '\[Wayland\] Renderer: .* on /dev/dri/' "$LOG"; then
  pass "Wayland renderer line present: $(grep -E '\[Wayland\] Renderer' "$LOG" | head -1)"
else
  fail "no '[Wayland] Renderer: ... on /dev/dri/...' line"
fi
ENC="$(grep -E '\[pixelflux\] Render node .* encodes' "$LOG" | head -1 || true)"
if [ -z "$ENC" ]; then
  fail "no '[pixelflux] Render node N encodes ...' line"
elif printf '%s' "$ENC" | grep -qiE 'nvenc|nvidia|cuda'; then
  pass "hardware encoder is NVIDIA: $ENC"
else
  fail "hardware encoder is NOT NVIDIA (vaapi/software fallback): $ENC"
fi
if grep -qiE 'llvmpipe|softpipe|swrast' "$LOG"; then
  fail "software GL renderer mentioned in log (llvmpipe/swrast)"
fi
if grep -qiE 'rendering in software \(Pixman\)|GPU renderer failed to initialize' "$LOG"; then
  fail "compositor fell back to software (Pixman): $(grep -iE 'GPU renderer failed|Pixman' "$LOG" | head -1 | cut -c1-160)"
  grep -qE 'DRM_IOCTL_MODE_CREATE_DUMB failed: Permission denied' "$LOG" && \
    echo "  hint: EACCES on CREATE_DUMB = mesa GBM fell back to dumb buffers on a render node; the NVIDIA GBM backend (nvidia-drm_gbm.so / libnvidia-egl-gbm) is not loaded in the container"
fi
if grep -qiE 'error|failed|cannot' "$LOG"; then
  echo "note: log contains error/failed lines (informational):"
  grep -iE 'error|failed|cannot' "$LOG" | head -10 | cut -c1-200
fi

hdr "GBM / EGL backend diagnostics (host)"
ls -l /usr/lib/x86_64-linux-gnu/gbm/ /usr/lib64/gbm/ 2>/dev/null || echo "no host gbm backend dir"
dpkg -l 2>/dev/null | awk '/egl-gbm|libgbm|libnvidia-egl|egl-wayland/ {print $2, $3}' || true
hdr "GBM / EGL backend diagnostics (container)"
docker exec "$NAME" sh -c '
  echo "-- gbm backends:"; ls -l /usr/lib/x86_64-linux-gnu/gbm/ 2>&1
  echo "-- egl vendors:"; ls /usr/share/glvnd/egl_vendor.d/ /etc/glvnd/egl_vendor.d/ 2>&1
  echo "-- nvidia egl/gbm libs:"; ldconfig -p | grep -iE "nvidia-egl|egl-gbm|libgbm" || echo "none"
  echo "-- /dev/dri:"; ls -ln /dev/dri
  echo "-- abc user:"; id abc 2>/dev/null || id
  echo "-- card0 open as abc:"; su -s /bin/sh abc -c "cat /dev/dri/card0 </dev/null >/dev/null 2>&1 && echo ok || echo DENIED" 2>/dev/null || echo "n/a"
' 2>&1 | cut -c1-200 || true

hdr "RESULT"
if [ "$FAILS" -eq 0 ]; then
  echo "ALL PASS"
else
  echo "${FAILS} FAIL line(s) above"
fi
echo
echo "Also by hand: run with SECS=3600 (second arg) and open http://127.0.0.1:${PORT}/studio/ from the host to"
echo "confirm the 3D view is smooth; the container is removed when the script exits."
echo "Paste this whole output back."
