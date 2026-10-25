"""v4 scoped API keys: scopes/printer allowlist/expiry/rate limit on api_tokens

Revision ID: b7d3f1a9c2e4
Revises: a4c6e8b1d3f5
Create Date: 2026-10-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7d3f1a9c2e4"
down_revision: str | Sequence[str] | None = "a4c6e8b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column(
        "api_tokens", sa.Column("printer_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True)
    )
    op.add_column("api_tokens", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_tokens", sa.Column("rate_limit_per_min", sa.Integer(), nullable=True))
    op.add_column("api_tokens", sa.Column("key_prefix", sa.String(), nullable=True))


def downgrade() -> None:
    for col in ("key_prefix", "rate_limit_per_min", "expires_at", "printer_ids", "scopes"):
        op.drop_column("api_tokens", col)
