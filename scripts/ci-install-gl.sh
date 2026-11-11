#!/usr/bin/env bash
# Install f3d's headless GL runtime + ffmpeg on a GitHub runner, resilient to
# Ubuntu mirror outages. Used by ci.yml and release.yml.
#
#   APT_DEBS_DIR   dir holding .debs cached by actions/cache (default ~/apt-debs)
#   APT_CACHE_HIT  "true" when that cache restored: try an offline dpkg install
#                  first; on any failure fall through to the apt path.
#
# apt path: drop third-party sources (only Ubuntu packages are needed, and a
# stalled extra source hangs `apt-get update`), probe candidate Ubuntu mirrors
# with a short curl and pin the first reachable one, then retry update+install.
# Newly downloaded .debs are copied to APT_DEBS_DIR so the cache can save them.
set -uo pipefail

PKGS=(libosmesa6 libgl1-mesa-dri libgl1 ffmpeg)
DEBS="${APT_DEBS_DIR:-$HOME/apt-debs}"

if [ "${APT_CACHE_HIT:-}" = "true" ] && compgen -G "$DEBS/*.deb" >/dev/null; then
  echo "apt cache hit: offline install from $DEBS"
  if sudo dpkg -i "$DEBS"/*.deb; then
    exit 0
  fi
  echo "::warning::offline install failed; falling back to apt"
fi

printf '%s\n' 'Acquire::Retries "3";' 'Acquire::http::Timeout "15";' 'Acquire::https::Timeout "15";' \
  | sudo tee /etc/apt/apt.conf.d/80-retries >/dev/null

# Ubuntu sources only.
for f in /etc/apt/sources.list.d/*; do
  case "$f" in */ubuntu.sources | *.disabled) ;; *) sudo mv "$f" "$f.disabled" 2>/dev/null || true ;; esac
done

codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
mirrors=(
  "" # the runner's default (Azure mirror list)
  http://archive.ubuntu.com/ubuntu
  https://mirrors.edge.kernel.org/ubuntu
  http://mirror.us.leaseweb.net/ubuntu
  https://mirror.arizona.edu/ubuntu
  https://mirrors.xmission.com/ubuntu
  http://us.archive.ubuntu.com/ubuntu
)

pin_mirror() { # $1 = mirror URL; rewrite every Ubuntu source to it
  sudo sed -i -E "s#(mirror\+file:/etc/apt/apt-mirrors\.txt|https?://[a-z0-9.-]*ubuntu\.com/ubuntu/?|https?://mirror[a-z0-9./-]*/ubuntu/?)#$1/#" \
    /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list 2>/dev/null || true
}

reachable() { # $1 = mirror URL ("" = default list, assumed ok)
  [ -z "$1" ] && return 0
  curl -fsS -m 8 -o /dev/null "$1/dists/$codename/InRelease" 2>/dev/null
}

ok=
for attempt in 1 2 3 4 5 6 7; do
  m="${mirrors[$(((attempt - 1) % ${#mirrors[@]}))]}"
  if ! reachable "$m"; then
    echo "mirror ${m:-default} unreachable; skipping"
    continue
  fi
  [ -n "$m" ] && pin_mirror "${m%/}"
  echo "apt attempt $attempt via ${m:-default}"
  if sudo timeout 60 apt-get update \
    && sudo timeout 240 apt-get install -y --no-install-recommends "${PKGS[@]}"; then
    ok=1
    break
  fi
  echo "::warning::apt attempt $attempt failed or timed out; retrying"
  sleep 3
done
[ -n "$ok" ] || { echo "::error::apt failed on every mirror"; exit 1; }

mkdir -p "$DEBS"
cp /var/cache/apt/archives/*.deb "$DEBS"/ 2>/dev/null || true
