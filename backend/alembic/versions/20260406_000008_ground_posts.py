from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_000008"
down_revision = "20260406_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ground_posts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("post_type", sa.String(32), nullable=False),
        sa.Column("ref_id", sa.String(36), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("extra_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "ground_post_likes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("ground_posts.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("post_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("ground_post_likes")
    op.drop_table("ground_posts")
