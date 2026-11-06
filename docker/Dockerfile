# Build from the REPO ROOT: `docker build -f docker/Dockerfile .`
# (compose.yaml sets `context: .` / `dockerfile: docker/Dockerfile` for
# exactly this reason -- the image needs both backend/ and web/.)

# ---- Stage 1: build the frontend SPA ---------------------------------
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS web-build

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
RUN npm run build

# ---- Stage 2: build gltfpack from source -------------------------------
# Source build, not the prebuilt gltfpack-ubuntu.zip release asset: that zip
# targets Ubuntu 24.04's glibc, which is newer than this image's trixie
# base -- copying it in would fail to run at all. Building from source on
# the SAME base image as the runtime stage guarantees glibc compatibility
# and, as a bonus, covers arm64 too (the prebuilt zip is amd64-only).
FROM python:3.12-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS gltfpack-build

RUN apt-get update && apt-get install -y --no-install-recommends git cmake g++ make

# Pinned to the commit v1.2 resolves to (a tag can be moved; a SHA cannot).
ARG MESHOPT_SHA=9d9890c73011d75920af614485296d1e03e95448
RUN git init /src && git -C /src fetch --depth 1 https://github.com/zeux/meshoptimizer "$MESHOPT_SHA" \
    && git -C /src checkout --detach FETCH_HEAD
RUN cmake -S /src -B /build -DMESHOPT_BUILD_GLTFPACK=ON -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build --target gltfpack -j"$(nproc)"

# ---- Stage 3: python runtime -------------------------------------------
FROM python:3.12-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS runtime

# uv is copied in as a static binary from its own distroless image (SPEC
# Task 9), not pip-installed.
#
# Pinned, not `:latest`: a floating tag means the resolver that produced
# backend/uv.lock can silently differ from the one consuming it, and makes
# a rebuild of an old release tag non-reproducible. Bump deliberately, and
# keep in step with UV_VERSION in .github/workflows/*.yml.
COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /usr/local/bin/uv

# GL runtime libs (RESEARCH §4 / Global Constraints "Local prerequisites"):
# - libosmesa6 + libgl1-mesa-dri: f3d software rendering -- this container
#   has no GPU, so f3d renders via OSMesa (dev hosts instead use f3d's
#   EGL-offscreen fallback, see app/pipeline/render.py, and need neither).
#   f3d dlopens the UNVERSIONED "libOSMesa.so", which only ships in
#   libosmesa6-dev -- the symlink below supplies just that one name without
#   dragging in dev headers.
# - libgl1: OCP (cadquery-ocp) links against libGL.so.1 at import time,
#   which libgl1-mesa-dri alone does not provide (verified: without it,
#   `from OCP.BRep import ...` dies with "libGL.so.1: cannot open shared
#   object file" and takes the api/worker down at startup).
# - gosu: the entrypoint's optional PUID/PGID privilege drop (Task 9) execs
#   into it instead of `su`/`sudo` -- no PAM/tty baggage, correct signal
#   forwarding as PID 1.
# - ffmpeg: P4 camera plan Task 7 transcodes a fetched .avi timelapse to
#   .mp4 on the cpu queue (`app.tasks.media.transcode_timelapse`); missing
#   ffmpeg degrades gracefully (keeps the .avi) rather than failing, but
#   it's cheap enough to just ship in the shared image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libosmesa6 libgl1-mesa-dri libgl1 gosu ffmpeg postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "/usr/lib/$(uname -m)-linux-gnu/libOSMesa.so.8" \
        "/usr/lib/$(uname -m)-linux-gnu/libOSMesa.so" \
    && ldconfig

# Base non-root user the entrypoint's privilege drop remaps to the
# configured PUID/PGID at container start (default 1000:1000; PUID=0 keeps root).
RUN groupadd -g 1000 tdmm && useradd -u 1000 -g tdmm -M -s /usr/sbin/nologin tdmm \
    && mkdir -p /home/tdmm /var/cache/tdmm \
    && chown tdmm:tdmm /home/tdmm && chmod 1777 /var/cache/tdmm

# f3d (and anything else XDG-aware) writes ~/.cache/f3d; $HOME may be missing or
# unwritable after a PUID remap, so point the cache at a world-writable dir.
ENV XDG_CACHE_HOME=/var/cache/tdmm MPLCONFIGDIR=/var/cache/tdmm/matplotlib

COPY --from=gltfpack-build /build/gltfpack /usr/local/bin/gltfpack

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    STATIC_DIR=/app/static

WORKDIR /app

# Dependencies first, in their own layer, so editing app code doesn't
# invalidate the (slow) dependency install.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Now the rest of the backend (app/, alembic/, alembic.ini, ...).
# backend/tests and backend/tests_e2e are excluded from the build context
# entirely via .dockerignore -- they have no business in the runtime image.
COPY backend/ .

# The built SPA, served by app/static.py's SPA-fallback route.
COPY --from=web-build /web/dist ./static

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Version stamp. CI passes --build-arg APP_VERSION=<semver> on release
# builds and APP_VERSION=edge-<short sha> on every other push to main; a
# plain local `docker build` gets `dev`. Read back at runtime by
# app.config.Settings and surfaced via FastAPI(version=...) +
# GET /api/health.
#
# Placement is load-bearing: an ARG/ENV invalidates every layer BELOW it,
# and this value changes on literally every build. It therefore has to sit
# under the expensive layers -- the apt/GL install, `uv sync --frozen`
# (minutes), the gltfpack COPY, the backend and static COPYs -- or the
# cache is worthless. It is declared ONLY in this stage, so web-build and
# gltfpack-build keep caching.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# The entrypoint drops privileges to PUID:PGID (default 1000:1000) via gosu
# and chowns the /data, /library and /exchange volumes on first start so
# volumes created by an older root-running release keep working. Set PUID=0
# to stay root (the virtual-printer service does: it binds ports < 1024).
# See docker/entrypoint.sh.

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
