#!/usr/bin/env bash
# Container entrypoint: switches on ROLE (SPEC "Architecture" -- one
# shared image, two entrypoints in M1; `printerd` is M4).
set -euo pipefail

# Privilege drop (linuxserver.io convention). NON-ROOT BY DEFAULT: when started
# as root the entrypoint remaps the `tdmm` user to PUID:PGID (default
# 1000:1000), fixes ownership of the volumes, and re-execs through gosu.
# PUID=0 opts out and stays root (the virtual-printer service needs this: it
# binds privileged ports). Re-exec guard avoids an infinite loop.
if [[ -z "${PRIVDROP_DONE:-}" && "$(id -u)" == "0" ]]; then
  PUID="${PUID:-1000}"; PGID="${PGID:-1000}"
  if [[ "$PUID" != "0" ]]; then
    groupmod -o -g "$PGID" tdmm 2>/dev/null || groupadd -o -g "$PGID" tdmm
    usermod  -o -u "$PUID" -g "$PGID" tdmm 2>/dev/null || useradd -o -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin tdmm
    # Volumes written by an older (root) release are root-owned: take them over.
    # The recursive chown only runs when the top-level owner is wrong, so a
    # healthy multi-GB /library is not re-walked on every restart. /data is
    # small, so any stray root-owned files inside it are always fixed.
    fix_owner() {
      [[ -e "$1" ]] || return 0
      if [[ "$(stat -c %u:%g "$1")" != "$PUID:$PGID" ]]; then
        echo "[entrypoint] chown -R $PUID:$PGID $1 (first start after a root-owned release)"
        chown -R "$PUID:$PGID" "$1" || echo "[entrypoint] WARNING: chown of $1 failed; writes may fail" >&2
      fi
    }
    fix_owner /library
    fix_owner /watch
    # OrcaSlicer exchange folder (shared with the orcaslicer sidecar; same PUID/PGID).
    mkdir -p /exchange 2>/dev/null || true
    fix_owner /exchange
    if [[ -d /data ]]; then
      find /data -xdev \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
    fi
    mkdir -p "${XDG_CACHE_HOME:-/var/cache/tdmm}" /home/tdmm 2>/dev/null || true
    chmod 1777 "${XDG_CACHE_HOME:-/var/cache/tdmm}" 2>/dev/null || true
    chown tdmm:tdmm /home/tdmm 2>/dev/null || true
    export PRIVDROP_DONE=1
    exec gosu tdmm:tdmm "$0" "$@"
  fi
fi

# Explicit command override (e.g. `celery ... beat` for the optional
# scheduled-scan service, or the PUID/PGID smoke check in the README) --
# skips the ROLE routing below and runs the given command directly,
# as whichever user the privilege drop above landed on (tdmm by default).
if [[ $# -gt 0 ]]; then
  exec "$@"
fi

case "${ROLE:-api}" in
  api)
    echo "[entrypoint] running database migrations..."
    alembic upgrade head
    echo "[entrypoint] starting API server..."
    # --proxy-headers lets the request scheme (and so the cookie Secure flag) follow
    # X-Forwarded-Proto from the trusted proxy IPs (FORWARDED_ALLOW_IPS; uvicorn
    # defaults to 127.0.0.1 only).
    exec uvicorn app.main:app --host 0.0.0.0 --port 8080 \
      --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
    ;;
  worker)
    echo "[entrypoint] starting Celery worker (queues: ${QUEUES:-io,cpu})..."
    ARGS=(-Q "${QUEUES:-io,cpu}" -l info)
    [[ -n "${CONCURRENCY:-}" ]] && ARGS+=(--concurrency="${CONCURRENCY}")
    [[ -n "${MAX_TASKS_PER_CHILD:-}" ]] && ARGS+=(--max-tasks-per-child="${MAX_TASKS_PER_CHILD}")
    [[ -n "${MAX_MEMORY_PER_CHILD:-}" ]] && ARGS+=(--max-memory-per-child="${MAX_MEMORY_PER_CHILD}")
    exec celery -A app.tasks.celery_app worker "${ARGS[@]}"
    ;;
  *)
    echo "[entrypoint] unknown ROLE=${ROLE:-<unset>} (expected 'api' or 'worker')" >&2
    exit 1
    ;;
esac
