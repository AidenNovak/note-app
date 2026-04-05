from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260329_000004"
down_revision = "20260329_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("insight_agent_runs") as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("model_name", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("api_duration_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("total_cost_usd", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("input_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("insight_agent_runs") as batch_op:
        batch_op.drop_column("output_tokens")
        batch_op.drop_column("input_tokens")
        batch_op.drop_column("total_cost_usd")
        batch_op.drop_column("api_duration_ms")
        batch_op.drop_column("duration_ms")
        batch_op.drop_column("model_name")
        batch_op.drop_column("session_id")
