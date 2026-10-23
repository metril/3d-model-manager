# Slicer sidecar: FastAPI wrapper around the Bambu Studio CLI (AppImage,
# extracted -- no FUSE needed). Build: `docker build slicer/`.
FROM ubuntu:24.04

ARG BAMBU_VERSION=v02.08.02.61
ARG BAMBU_BUILD=20260820225108
ARG BAMBU_SHA256=d501b103fac5424513ec0e8d6bc145fb30719de2c7d94d7320d723740c81a7fd

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xvfb xauth python3 python3-venv \
        libgtk-3-0t64 libwebkit2gtk-4.1-0 libgl1 libglu1-mesa libegl1 libosmesa6 \
        libdbus-1-3 libsecret-1-0 libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
        libsoup-3.0-0 libmspack0t64 libnotify4 libsm6 fonts-dejavu-core locales \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/bs.AppImage \
        "https://github.com/bambulab/BambuStudio/releases/download/${BAMBU_VERSION}/BambuStudio_ubuntu24.04-${BAMBU_VERSION}-${BAMBU_BUILD}.AppImage" \
    && echo "${BAMBU_SHA256}  /tmp/bs.AppImage" | sha256sum -c - \
    && chmod +x /tmp/bs.AppImage && cd /opt && /tmp/bs.AppImage --appimage-extract >/dev/null \
    && mv squashfs-root bambu && rm /tmp/bs.AppImage

RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir \
        fastapi==0.115.* "uvicorn[standard]==0.34.*" python-multipart==0.0.20

COPY app /srv/app
RUN useradd -u 10001 -m slicer && mkdir -p /home/slicer/.config && chown -R slicer /home/slicer
USER 10001
WORKDIR /srv
ENV BAMBU_DIR=/opt/bambu BAMBU_VERSION=${BAMBU_VERSION} HOME=/home/slicer LC_ALL=C \
    LD_LIBRARY_PATH=/opt/bambu/bin PATH=/opt/venv/bin:$PATH SLICER_USE_XVFB=0 SLICER_MAX_JOBS=1 SLICER_JOB_TIMEOUT_S=600
EXPOSE 3001
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python3 -c "import urllib.request as u; u.urlopen('http://localhost:3001/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
