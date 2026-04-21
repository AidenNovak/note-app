"""drop insight_agent_runs and insight_generation_logs

Revision ID: 0e5e1856db3c
Revises: 86a501eb1189
Create Date: 2026-04-21 23:13:34.070171
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0e5e1856db3c"
down_revision = "86a501eb1189"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop insight_generation_logs (with indexes)
    op.drop_index(
        "ix_insight_generation_logs_generation_id_event_index",
        table_name="insight_generation_logs",
    )
    op.drop_index(
        "ix_insight_generation_logs_generation_id",
        table_name="insight_generation_logs",
    )
    op.drop_table("insight_generation_logs")

    # Drop insight_agent_runs (with index)
    op.drop_index(
        "ix_insight_agent_runs_generation_id",
        table_name="insight_agent_runs",
    )
    op.drop_table("insight_agent_runs")


def downgrade() -> None:
    # Recreate insight_agent_runs
    op.create_table(
        "insight_agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("generation_id", sa.String(length=36), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("api_duration_ms", sa.Integer(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["generation_id"], ["insight_generations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_insight_agent_runs_generation_id",
        "insight_agent_runs",
        ["generation_id"],
        unique=False,
    )

    # Recreate insight_generation_logs
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
