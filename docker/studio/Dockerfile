# Bambu Studio GUI sidecar: the linuxserver Selkies image (labwc + Xwayland,
# streamed over WebSocket) plus a tiny launcher service that relaunches Studio
# on a file under /exchange. Pinned by digest; this is
# lscr.io/linuxserver/bambustudio:latest as of 2026-10-31 (Selkies 2.1.0).
FROM lscr.io/linuxserver/bambustudio@sha256:857e937fe1c3451244cb178952e0e7a00241ab48c32cb1ce9ea6b27e4fd729e5

COPY root/ /
RUN chmod +x /app/launcher.py /custom-cont-init.d/10-studio-prefs \
    /etc/s6-overlay/s6-rc.d/svc-launcher/run \
 && mkdir -p /exchange

EXPOSE 3000 3010
