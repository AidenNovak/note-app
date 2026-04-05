from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_000006"
down_revision = "20260404_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_embeddings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("note_id", sa.String(36), sa.ForeignKey("notes.id"), nullable=False, unique=True, index=True),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("model", sa.String(128), nullable=False, server_default="openai/text-embedding-3-small"),
        sa.Column("dimension", sa.Integer(), nullable=False, server_default="1536"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "note_similarities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("note_id", sa.String(36), sa.ForeignKey("notes.id"), nullable=False, index=True),
        sa.Column("similar_note_id", sa.String(36), sa.ForeignKey("notes.id"), nullable=False, index=True),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("note_id", "similar_note_id"),
    )


def downgrade() -> None:
    op.drop_table("note_similarities")
    op.drop_table("note_embeddings")
