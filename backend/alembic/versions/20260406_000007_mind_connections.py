from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_000007"
down_revision = "20260406_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mind_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("note_a_id", sa.String(36), sa.ForeignKey("notes.id"), nullable=False, index=True),
        sa.Column("note_b_id", sa.String(36), sa.ForeignKey("notes.id"), nullable=False, index=True),
        sa.Column("shared_tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("similarity_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("connection_type", sa.String(32), nullable=False, server_default="tag_cooccurrence"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("note_a_id", "note_b_id"),
    )


def downgrade() -> None:
    op.drop_table("mind_connections")
