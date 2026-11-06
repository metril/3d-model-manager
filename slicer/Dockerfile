# Slicer sidecar: FastAPI wrapper around the OrcaSlicer CLI (AppImage,
# extracted -- no FUSE needed). Build: `docker build slicer/`.
FROM ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c

# uv installs the pinned deps from uv.lock (same uv as docker/Dockerfile).
COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /usr/local/bin/uv

ARG ORCA_VERSION=v2.4.2
ARG ORCA_SHA256=d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xvfb xauth python3 \
        libgtk-3-0t64 libwebkit2gtk-4.1-0 libgl1 libglu1-mesa libegl1 libosmesa6 libopengl0 libgl1-mesa-dri \
        libdbus-1-3 libsecret-1-0 libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
        libsoup-3.0-0 libmspack0t64 libnotify4 libsm6 fonts-dejavu-core locales \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/orca.AppImage \
        "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/${ORCA_VERSION}/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION#v}.AppImage" \
    && echo "${ORCA_SHA256}  /tmp/orca.AppImage" | sha256sum -c - \
    && chmod +x /tmp/orca.AppImage && cd /opt && /tmp/orca.AppImage --appimage-extract >/dev/null \
    && mv squashfs-root orca && rm /tmp/orca.AppImage

COPY pyproject.toml uv.lock /srv/
RUN cd /srv && UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON=/usr/bin/python3 UV_LINK_MODE=copy \
        UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev --no-install-project

COPY app /srv/app
RUN useradd -u 10001 -m slicer && mkdir -p /home/slicer/.config && chown -R slicer /home/slicer
USER 10001
WORKDIR /srv
ENV SLICER_DIR=/opt/orca SLICER_VERSION=${ORCA_VERSION} HOME=/home/slicer LC_ALL=C \
    LD_LIBRARY_PATH=/opt/orca/lib/orca-runtime:/opt/orca/bin PATH=/opt/venv/bin:$PATH SLICER_USE_XVFB=1 SLICER_MAX_JOBS=1 SLICER_JOB_TIMEOUT_S=600
EXPOSE 3001
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python3 -c "import urllib.request as u; u.urlopen('http://localhost:3001/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
