"""Zero-config auth for the OrcaSlicer launcher.

Precedence: an explicit token (env ``TDMM_ORCASLICER_LAUNCHER_TOKEN`` or the
Settings -> Server value, both surfaced as ``cfg.orcaslicer_launcher_token``)
wins. Otherwise the shared secret lives in ``<exchange>/.launcher-token``
(mode 0600), generated once by the api; the launcher reads the same file when
its own env var is empty. The scanner skips dotfiles in the exchange root.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path

from app.config import get_settings
from app.services.server_config import ServerConfig

TOKEN_FILENAME = ".launcher-token"


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def shared_token(exchange_dir: Path) -> str | None:
    """The token in the exchange dir, creating it (atomically, 0600) when absent."""
    path = exchange_dir / TOKEN_FILENAME
    if (tok := _read(path)) is not None:
        return tok
    tok = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read(path)  # lost a creation race: use the winner's
    except OSError:
        return None  # exchange dir missing / read-only
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    return tok


def launcher_headers(cfg: ServerConfig) -> dict[str, str]:
    """``X-Launcher-Token`` header for calls to the launcher ({} if no token at all)."""
    if cfg.orcaslicer_launcher_token is not None:
        return {"X-Launcher-Token": cfg.orcaslicer_launcher_token.get_secret_value()}
    tok = None
    with contextlib.suppress(Exception):
        tok = shared_token(get_settings().orcaslicer_exchange_dir)
    return {"X-Launcher-Token": tok} if tok else {}
