# OrcaSlicer GUI sidecar: the linuxserver Selkies image (labwc + Xwayland,
# streamed over WebSocket) plus a tiny launcher service that relaunches the
# slicer on a file under /exchange. Pinned by digest; this is
# lscr.io/linuxserver/orcaslicer:v2.4.2-ls46 (OrcaSlicer v2.4.2).
FROM lscr.io/linuxserver/orcaslicer@sha256:b8806fab27725e26ddd14a24f22990bf0780bc7fdadb43e23120b9a1c16c8b88

COPY root/ /
RUN chmod +x /app/launcher.py /custom-cont-init.d/10-orcaslicer-prefs /custom-cont-init.d/15-orcaslicer-autostart /custom-cont-init.d/20-nvidia-gbm \
    /etc/s6-overlay/s6-rc.d/svc-launcher/run \
 && mkdir -p /exchange

# Load the app-theme sync script from the Selkies dashboard page; fail the build if the
# base image's markup no longer has the </head> anchor.
RUN f=/usr/share/selkies/selkies-dashboard/index.html \
 && (grep -q tdmm-theme-sync "$f" || sed -i 's#</head>#<script src="tdmm-theme-sync.js" defer></script></head>#' "$f") \
 && grep -q tdmm-theme-sync "$f"

EXPOSE 3000 3010
