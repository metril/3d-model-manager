"""Bambu firmware feed (ported from bambuddy's ``firmware_check``): the
public wiki release-history page is the version source (always current), the
bambulab.com download page JSON supplies the image URL + release notes when
reachable (Cloudflare may block it -> wiki-only, no download URL).

Latest-version data is cached in the ``settings`` table under
``firmware_latest`` by the daily beat task. We only ever STAGE an image on the
printer's SD card; flashing is always triggered on the printer itself.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.models import Setting

CACHE_KEY = "firmware_latest"
WIKI_BASE = "https://wiki.bambulab.com"
BAMBU_BASE = "https://bambulab.com"
FIRMWARE_PAGE = "/en/support/firmware-download/all"
UA = {"User-Agent": "3d-model-manager/1.0"}

MODEL_TO_API_KEY = {
    "X1": "x1", "X1C": "x1", "X1-CARBON": "x1", "X1 CARBON": "x1", "BL-P001": "x1",
    "BL-P002": "x1", "X1E": "x1e", "C13": "x1e", "BL-P003": "x1e",
    "P1P": "p1", "P1S": "p1", "C11": "p1", "C12": "p1",
    "A1": "a1", "N2S": "a1", "A1 MINI": "a1-mini", "A1-MINI": "a1-mini", "A1MINI": "a1-mini",
    "N1": "a1-mini", "A2L": "a2l", "N9": "a2l",
    "H2D": "h2d", "O1D": "h2d", "H2D PRO": "h2d-pro", "H2D-PRO": "h2d-pro", "H2DPRO": "h2d-pro",
    "O1E": "h2d-pro", "O2D": "h2d-pro", "H2C": "h2c", "O1C": "h2c", "O1C2": "h2c",
    "H2S": "h2s", "O1S": "h2s", "P2S": "p2s", "N7": "p2s", "X2D": "x2d", "N6": "x2d",
}  # fmt: skip

WIKI_PATHS = {
    "x1": "/en/x1/manual/X1-X1C-firmware-release-history",
    "x1e": "/en/x1/manual/X1E-firmware-release-history",
    "p1": "/en/p1/manual/p1p-firmware-release-history",
    "a1": "/en/a1/manual/a1-firmware-release-history",
    "a1-mini": "/en/a1-mini/manual/a1-mini-firmware-release-history",
    "h2d": "/en/h2d/manual/h2d-firmware-release-history",
    "h2c": "/en/h2c/manual/h2c-firmware-release-history",
    "h2s": "/en/h2s/manual/h2s-firmware-release-history",
    "p2s": "/en/p2s/manual/p2s-firmware-release-history",
    "x2d": "/en/x2d/manual/x2d-firmware-release-history",
    "h2d-pro": "/en/h2d-pro/manual/firmware-release-history",
    "a2l": "/en/a2l/manual/a2l-firmware-release-history",
}


def api_key_for(model: str | None) -> str | None:
    return MODEL_TO_API_KEY.get((model or "").strip().upper())


def version_tuple(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def update_available(current: str | None, latest: str | None) -> bool:
    c, latest_t = version_tuple(current), version_tuple(latest)
    return bool(c and latest_t and latest_t > c)


def wiki_versions(client: httpx.Client, api_key: str) -> list[tuple[str, str | None]]:
    """Versions from the wiki's heading anchors, newest first: (version, YYYYMMDD)."""
    path = WIKI_PATHS.get(api_key)
    if not path:
        return []
    try:
        r = client.get(f"{WIKI_BASE}{path}", follow_redirects=True)
    except httpx.HTTPError:
        return []
    if r.status_code != 200:
        return []
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for a, b, c, d, date in re.findall(r'id="h-(\d{2})(\d{2})(\d{2})(\d{2})-?(\d{8})"', r.text):
        v = f"{a}.{b}.{c}.{d}"
        if v not in seen:
            seen.add(v)
            out.append((v, date))
    if not out:  # heading text "XX.XX.XX.XX (YYYYMMDD)", ASCII or full-width parens
        for v, date in re.findall(r"(\d{2}\.\d{2}\.\d{2}\.\d{2})\s*[(（](\d{8})[)）]", r.text):
            if v not in seen:
                seen.add(v)
                out.append((v, date))
    return out


def download_page_versions(client: httpx.Client, api_key: str) -> list[dict]:
    """Versions (with image URL + notes) from bambulab.com's Next.js data; []
    when blocked/unreachable."""
    try:
        page = client.get(f"{BAMBU_BASE}{FIRMWARE_PAGE}", follow_redirects=True)
        m = re.search(r'"buildId":"([^"]+)"', page.text) if page.status_code == 200 else None
        if not m:
            return []
        r = client.get(
            f"{BAMBU_BASE}/_next/data/{m.group(1)}/en/support/firmware-download/{api_key}.json"
        )
        if r.status_code != 200:
            return []
        versions = r.json().get("pageProps", {}).get("printerMap", {}).get(api_key, {})
        versions = versions.get("versions", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [
        {
            "version": v["version"],
            "download_url": v.get("url") or "",
            "release_notes": v.get("release_notes_en"),
            "release_time": v.get("release_time"),
        }
        for v in versions
        if isinstance(v, dict) and v.get("version")
    ]


def fetch_latest(client: httpx.Client, api_key: str) -> dict | None:
    """Latest version for a model: wiki first, enriched from the download page."""
    wiki = wiki_versions(client, api_key)
    dl = download_page_versions(client, api_key)
    if wiki:
        version = wiki[0][0]
        match = next((d for d in dl if d["version"] == version), None)
        return {
            "version": version,
            "download_url": (match or {}).get("download_url", ""),
            "release_notes": (match or {}).get("release_notes"),
            "release_time": (match or {}).get("release_time") or wiki[0][1],
        }
    return dl[0] if dl else None


def refresh_cache(session: Session, models: list[str | None], client: httpx.Client) -> dict:
    """Fetch the latest firmware per distinct model api-key and persist."""
    row = session.get(Setting, CACHE_KEY)
    cache: dict = dict(row.value) if row else {}
    for key in sorted({k for m in models if (k := api_key_for(m))}):
        info = fetch_latest(client, key)
        if info:
            cache[key] = {**info, "checked_at": datetime.now(UTC).isoformat()}
    if row is None:
        session.add(Setting(key=CACHE_KEY, value=cache))
    else:
        row.value = cache
    session.commit()
    return cache


def make_client() -> httpx.Client:
    return httpx.Client(headers=UA, timeout=30.0)


def download_image(client: httpx.Client, url: str, dest_dir: Path) -> Path:
    """Stream the firmware image to ``dest_dir`` keeping its original filename
    (the printer matches on it)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(url.split("?", 1)[0]).name or "firmware.bin"
    final, tmp = dest_dir / name, dest_dir / f".dl_{name}"
    if final.exists():
        return final
    with client.stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(65536):
                fh.write(chunk)
    tmp.rename(final)
    return final


def upload_status_key(printer_id: int) -> str:
    return f"printer:{printer_id}:fw_upload"
