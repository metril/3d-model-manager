# OrcaSlicer GUI sidecar: the linuxserver Selkies image (labwc + Xwayland,
# streamed over WebSocket) plus a tiny launcher service that relaunches the
# slicer on a file under /exchange. Pinned by digest; this is
# lscr.io/linuxserver/orcaslicer:v2.4.2-ls46 (OrcaSlicer v2.4.2).
FROM lscr.io/linuxserver/orcaslicer@sha256:b8806fab27725e26ddd14a24f22990bf0780bc7fdadb43e23120b9a1c16c8b88

COPY root/ /
RUN chmod +x /app/launcher.py /custom-cont-init.d/10-studio-prefs /custom-cont-init.d/20-nvidia-gbm \
    /etc/s6-overlay/s6-rc.d/svc-launcher/run \
 && mkdir -p /exchange

EXPOSE 3000 3010
