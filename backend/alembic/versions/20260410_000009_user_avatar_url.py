from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260410_000009"
down_revision = "20260406_000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_url")
