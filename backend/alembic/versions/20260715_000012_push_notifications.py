"""Add push notification tables (device_tokens, notification_preferences, push_notification_logs)

Revision ID: 20260715_000012
Revises: 20260414_000011
"""
from alembic import op
import sqlalchemy as sa

revision = "20260715_000012"
down_revision = "20260414_000011"


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("token", sa.String(512), nullable=False, index=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("device_name", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "token"),
    )

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, unique=True, index=True),
        sa.Column("enabled", sa.Boolean(), default=True, nullable=False),
        sa.Column("post_liked", sa.Boolean(), default=True, nullable=False),
        sa.Column("note_liked", sa.Boolean(), default=True, nullable=False),
        sa.Column("insight_ready", sa.Boolean(), default=True, nullable=False),
        sa.Column("mind_connection", sa.Boolean(), default=True, nullable=False),
        sa.Column("milestone", sa.Boolean(), default=True, nullable=False),
        sa.Column("quiet_hours_start", sa.Integer(), nullable=True),
        sa.Column("quiet_hours_end", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "push_notification_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("type", sa.String(32), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.String(512), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), default="sent", nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("push_notification_logs")
    op.drop_table("notification_preferences")
    op.drop_table("device_tokens")
