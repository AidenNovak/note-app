"""Ground moderation — reports, user blocks, per-user post hides, admin takedown flags.

Adds:
  - ground_post_reports (user-reported posts, status: open/actioned/dismissed)
  - user_blocks (user-to-user block list)
  - ground_post_hides (per-user soft hide of a post)
  - ground_posts.is_hidden / hidden_reason / hidden_at (admin/server-side takedown)

Required for App Store Guideline 1.2 (UGC apps must provide mechanisms to report
and block). Without these routes the app will be rejected.

Revision ID: 20260417_000014
Revises: 20260417_000013
"""
import sqlalchemy as sa
from alembic import op


revision = "20260417_000014"
down_revision = "20260417_000013"


def upgrade() -> None:
    # ── ground_posts takedown columns ────────────────────────────────
    with op.batch_alter_table("ground_posts") as batch:
        batch.add_column(
            sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("hidden_reason", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_ground_posts_is_hidden", "ground_posts", ["is_hidden"], unique=False)

    # ── reports ──────────────────────────────────────────────────────
    op.create_table(
        "ground_post_reports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "post_id",
            sa.String(length=36),
            sa.ForeignKey("ground_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reporter_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewer_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("post_id", "reporter_id", name="uq_report_post_reporter"),
    )
    op.create_index("ix_ground_post_reports_post_id", "ground_post_reports", ["post_id"])
    op.create_index("ix_ground_post_reports_reporter_id", "ground_post_reports", ["reporter_id"])
    op.create_index("ix_ground_post_reports_status", "ground_post_reports", ["status"])
    op.create_index("ix_ground_post_reports_created_at", "ground_post_reports", ["created_at"])

    # ── user blocks ──────────────────────────────────────────────────
    op.create_table(
        "user_blocks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "blocker_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blocked_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_user_blocks_pair"),
    )
    op.create_index("ix_user_blocks_blocker_id", "user_blocks", ["blocker_id"])
    op.create_index("ix_user_blocks_blocked_id", "user_blocks", ["blocked_id"])

    # ── per-user post hides ──────────────────────────────────────────
    op.create_table(
        "ground_post_hides",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_id",
            sa.String(length=36),
            sa.ForeignKey("ground_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "post_id", name="uq_hide_user_post"),
    )
    op.create_index("ix_ground_post_hides_user_id", "ground_post_hides", ["user_id"])
    op.create_index("ix_ground_post_hides_post_id", "ground_post_hides", ["post_id"])


def downgrade() -> None:
    op.drop_index("ix_ground_post_hides_post_id", table_name="ground_post_hides")
    op.drop_index("ix_ground_post_hides_user_id", table_name="ground_post_hides")
    op.drop_table("ground_post_hides")

    op.drop_index("ix_user_blocks_blocked_id", table_name="user_blocks")
    op.drop_index("ix_user_blocks_blocker_id", table_name="user_blocks")
    op.drop_table("user_blocks")

    op.drop_index("ix_ground_post_reports_created_at", table_name="ground_post_reports")
    op.drop_index("ix_ground_post_reports_status", table_name="ground_post_reports")
    op.drop_index("ix_ground_post_reports_reporter_id", table_name="ground_post_reports")
    op.drop_index("ix_ground_post_reports_post_id", table_name="ground_post_reports")
    op.drop_table("ground_post_reports")

    op.drop_index("ix_ground_posts_is_hidden", table_name="ground_posts")
    with op.batch_alter_table("ground_posts") as batch:
        batch.drop_column("hidden_at")
        batch.drop_column("hidden_reason")
        batch.drop_column("is_hidden")
