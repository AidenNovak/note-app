from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260414_000010"
down_revision = "20260410_000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column(
            "ai_status",
            sa.String(16),
            server_default="idle",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("notes", "ai_status")
