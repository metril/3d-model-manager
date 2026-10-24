#!/usr/bin/env bash
# M1+M2+M3+M4 acceptance gate (Task 9, renamed/generalized by Task 10):
# builds and starts the full docker compose stack, waits for the api to
# become healthy, then runs every test under backend/tests_e2e/ against the
# real HTTP API -- currently the M1 upload/revision/diff/download/restart
# flow (test_m1_flow.py), the M2 processing-pipeline flow
# (test_m2_pipeline.py), the M3 scan relink/adopt flow (test_m3_scan.py),
# and the M4 printer flow (test_m4_printer.py: flag-off safety, then the
# flag flipped on to drive the setup wizard/CRUD, a Developer-Mode test
# probe that soft-fails with no hardware present, and the bare-`.gcode`/
# not-ready-printer send-flow rejections -- no printer hardware or MQTT
# broker involved) -- then tears the stack down. test_m4_printer.py flips the
# printer flag live via `PUT /api/settings/app`, so no `.env` edit or
# container recreate is needed. Only the core services are brought up (the
# always-on slicer/obico-ml/virtual-printer sidecars are not needed by the
# suite and would slow CI).
#
# Usage: scripts/e2e.sh [--keep-volumes]
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BASE_URL="${E2E_BASE_URL:-http://localhost:8080}"
HEALTH_URL="${BASE_URL}/api/health"
HEALTH_TIMEOUT_S=120
KEEP_VOLUMES=0

for arg in "$@"; do
  case "$arg" in
    --keep-volumes) KEEP_VOLUMES=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      echo "usage: $0 [--keep-volumes]" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "==> No .env found; copying .env.example (edit before real use)."
  cp .env.example .env
fi

# The e2e test needs a KNOWN admin password to log in with -- a randomly
# generated first-run password is only ever printed once to the api
# container's logs, which the test has no way to read back. Pin a fixed one
# into .env for this run if none is already set there.
if ! grep -qE '^ADMIN_PASSWORD=.+' .env; then
  echo "==> No ADMIN_PASSWORD set in .env; pinning one for this e2e run."
  grep -vE '^#?ADMIN_PASSWORD=' .env > .env.tmp && mv .env.tmp .env
  echo "ADMIN_PASSWORD=e2e-test-admin-password" >> .env
fi
ADMIN_PASSWORD="$(grep -E '^ADMIN_PASSWORD=' .env | tail -n1 | cut -d= -f2-)"
ADMIN_USERNAME="$(grep -E '^ADMIN_USERNAME=' .env | tail -n1 | cut -d= -f2-)"
export ADMIN_PASSWORD
export ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"

cleanup() {
  local status=$?
  echo "==> compose logs (api, tail 100) ----------------------------------"
  docker compose logs api --tail=100 || true
  echo "==> tearing down the stack..."
  if [[ "$KEEP_VOLUMES" == "1" ]]; then
    docker compose down
  else
    docker compose down --volumes
  fi
  exit "$status"
}
trap cleanup EXIT

# Explicit core list: `compose.yaml` includes the slicer / obico-ml sidecars
# (always-on, no profiles) and the virtual-printer; none are exercised here.
CORE_SERVICES=(db redis api worker-io worker-cpu beat printerd)

echo "==> Building and starting the stack..."
docker compose up -d --build "${CORE_SERVICES[@]}"

echo "==> Waiting for ${HEALTH_URL} (timeout ${HEALTH_TIMEOUT_S}s)..."
elapsed=0
until curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; do
  if (( elapsed >= HEALTH_TIMEOUT_S )); then
    echo "Timed out waiting for the api to become healthy." >&2
    docker compose ps
    exit 1
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done
echo "==> api is healthy."

echo "==> Running the e2e flow (backend/tests_e2e/)..."
E2E_BASE_URL="${BASE_URL}" uv run --project backend pytest backend/tests_e2e -q -m e2e
