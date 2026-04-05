from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260329_000003"
down_revision = "20260328_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("insight_generations") as batch_op:
        batch_op.add_column(
            sa.Column("workflow_version", sa.String(length=64), nullable=False, server_default="agentic-v2")
        )
        batch_op.add_column(sa.Column("session_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("workspace_path", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("insight_reports") as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=32), nullable=False, server_default="published"))
        batch_op.add_column(sa.Column("report_version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("importance_score", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("novelty_score", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("review_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("card_rank", sa.Integer(), nullable=False, server_default="0"))

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
        sa.ForeignKeyConstraint(["generation_id"], ["insight_generations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insight_agent_runs_generation_id", "insight_agent_runs", ["generation_id"], unique=False)

    op.create_table(
        "insight_evidence_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["insight_reports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insight_evidence_items_report_id", "insight_evidence_items", ["report_id"], unique=False)
    op.create_index("ix_insight_evidence_items_note_id", "insight_evidence_items", ["note_id"], unique=False)

    op.create_table(
        "insight_action_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["insight_reports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insight_action_items_report_id", "insight_action_items", ["report_id"], unique=False)


def downgrade() -> None:
    op.drop_table("insight_action_items")
    op.drop_table("insight_evidence_items")
    op.drop_table("insight_agent_runs")

    with op.batch_alter_table("insight_reports") as batch_op:
        batch_op.drop_column("card_rank")
        batch_op.drop_column("review_summary")
        batch_op.drop_column("novelty_score")
        batch_op.drop_column("importance_score")
        batch_op.drop_column("report_version")
        batch_op.drop_column("status")

    with op.batch_alter_table("insight_generations") as batch_op:
        batch_op.drop_column("is_active")
        batch_op.drop_column("summary")
        batch_op.drop_column("workspace_path")
        batch_op.drop_column("session_id")
        batch_op.drop_column("workflow_version")
