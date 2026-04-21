"""Persist insight generation timeline logs.

Revision ID: 20260421_000016
Revises: 20260417_000015
"""
from alembic import op
import sqlalchemy as sa


revision = "20260421_000016"
down_revision = "20260417_000015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insight_generation_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "generation_id",
            sa.String(length=36),
            sa.ForeignKey("insight_generations.id"),
            nullable=False,
        ),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("group_index", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_insight_generation_logs_generation_id",
        "insight_generation_logs",
        ["generation_id"],
    )
    op.create_index(
        "ix_insight_generation_logs_generation_id_event_index",
        "insight_generation_logs",
        ["generation_id", "event_index"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_insight_generation_logs_generation_id_event_index",
        table_name="insight_generation_logs",
    )
    op.drop_index(
        "ix_insight_generation_logs_generation_id",
        table_name="insight_generation_logs",
    )
    op.drop_table("insight_generation_logs")
