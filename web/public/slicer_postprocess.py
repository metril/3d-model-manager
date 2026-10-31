#!/usr/bin/env python3
"""OrcaSlicer / Bambu Studio post-processing script: hands a freshly-sliced file to a
running 3D Model Manager instance (Round 8 Task 4).

Setup (OrcaSlicer or Bambu Studio -> Process -> Others -> Post-processing scripts):

    python3 /path/to/slicer_postprocess.py

Configure it via two environment variables (set them in the shell/session
the slicer itself runs in -- the slicer does not let you pass extra
arguments of your own, only appends the sliced file's path as the last
argument):

    INTAKE_URL   e.g. http://your-host:8080/api/slicer/intake
    INTAKE_TOKEN  an API token minted from Settings -> Accounts

IMPORTANT CAVEAT: The slicer's post-processing hook hands this script a
plain ``.gcode`` file -- fine for capturing slicing metadata/print history,
but NOT something the app can send to a printer (the app's "send to
printer" flow needs a sliced ``.gcode.3mf`` plate file). For a file you
actually want to print from the app, use the slicer's
File -> Export -> Export plate sliced file (``.gcode.3mf``) into the
folder this instance watches, instead of relying on this script alone.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT_S = 60
_BODY_SNIPPET_LEN = 500


def main(argv: list[str]) -> int:
    # The slicer appends the sliced file's absolute path as the LAST
    # argument -- everything before it (if anything) is whatever else was
    # configured on the same command line, which this script ignores.
    if not argv:
        print("slicer_postprocess: missing gcode path argument", file=sys.stderr)
        return 1
    gcode_path = argv[-1]

    url = os.environ.get("INTAKE_URL")
    token = os.environ.get("INTAKE_TOKEN")
    if not url:
        print("slicer_postprocess: INTAKE_URL is not set", file=sys.stderr)
        return 1
    if not token:
        print("slicer_postprocess: INTAKE_TOKEN is not set", file=sys.stderr)
        return 1

    try:
        with open(gcode_path, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        print(f"slicer_postprocess: could not read {gcode_path!r}: {exc}", file=sys.stderr)
        return 1

    filename = os.path.basename(gcode_path)
    query = urllib.parse.urlencode({"filename": filename})
    separator = "&" if "?" in url else "?"
    request_url = f"{url}{separator}{query}"

    request = urllib.request.Request(
        request_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            status = response.status
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_body = exc.read()
    except urllib.error.URLError as exc:
        print(f"slicer_postprocess: request failed: {exc.reason}", file=sys.stderr)
        return 1

    if status < 200 or status >= 300:
        snippet = response_body[:_BODY_SNIPPET_LEN].decode("utf-8", errors="replace")
        print(f"slicer_postprocess: intake failed (HTTP {status}): {snippet}", file=sys.stderr)
        return 1

    print(f"slicer_postprocess: sent {filename!r} to {url} (HTTP {status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
