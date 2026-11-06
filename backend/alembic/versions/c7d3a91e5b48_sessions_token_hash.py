"""sessions.token_hash: store sha256 of the cookie token, not the token

Existing sessions stay valid: their hash is backfilled from the old plaintext
id, then the id is rotated so the stored value is no longer a usable cookie.

Revision ID: c7d3a91e5b48
Revises: b8e4c2d6f0a1
Create Date: 2026-11-06 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d3a91e5b48"
down_revision: str | Sequence[str] | None = "b8e4c2d6f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("token_hash", sa.String(), nullable=True))
    op.execute(
        "UPDATE sessions SET token_hash = encode(sha256(convert_to(id::text, 'UTF8')), 'hex'), "
        "id = gen_random_uuid()"
    )
    op.alter_column("sessions", "token_hash", nullable=False)
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)


def downgrade() -> None:
    # Cookies can't be recovered from hashes: drop every session.
    op.execute("DELETE FROM sessions")
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_column("sessions", "token_hash")
