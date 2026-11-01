"""rename studio_* server settings to orcaslicer_*

The GUI slicer sidecar was renamed from "studio" to "orcaslicer". The ``server``
row in ``settings`` is a JSON blob, so the keys (and the ``_seeded_from_env``
field-name list) are renamed in place. The old default hostnames
(``http://studio:3000`` / ``:3010``) follow the renamed compose service.

Revision ID: c7d1e3a5b9f2
Revises: b5c9d2e7f3a4
Create Date: 2026-10-31 12:00:00.000000

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d1e3a5b9f2"
down_revision: str | Sequence[str] | None = "b5c9d2e7f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FIELDS = ("enabled", "url", "launcher_url", "launcher_token")
# field -> (old default, new default)
_URLS = {
    "url": ("http://studio:3000", "http://orcaslicer:3000"),
    "launcher_url": ("http://studio:3010", "http://orcaslicer:3010"),
}


def _rewrite(value: dict, old: str, new: str, up: bool) -> dict:
    out = dict(value)
    for f in _FIELDS:
        if f"{old}_{f}" not in out:
            continue
        v = out.pop(f"{old}_{f}")
        pair = _URLS.get(f)
        if pair and v == pair[0 if up else 1]:
            v = pair[1 if up else 0]
        out[f"{new}_{f}"] = v
    seeded = out.get("_seeded_from_env")
    if isinstance(seeded, list):
        out["_seeded_from_env"] = [
            f"{new}_{s[len(old) + 1 :]}" if isinstance(s, str) and s.startswith(f"{old}_") else s
            for s in seeded
        ]
    return out


def _migrate(old: str, new: str, up: bool) -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT value FROM settings WHERE key = 'server'")).first()
    if row is None:
        return
    value = row[0]
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        return
    bind.execute(
        sa.text("UPDATE settings SET value = CAST(:v AS jsonb) WHERE key = 'server'"),
        {"v": json.dumps(_rewrite(value, old, new, up))},
    )


def upgrade() -> None:
    _migrate("studio", "orcaslicer", True)


def downgrade() -> None:
    _migrate("orcaslicer", "studio", False)
