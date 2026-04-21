"""add_insight_event_and_agent_state

Revision ID: 86a501eb1189
Revises: 20260421_000016
Create Date: 2026-04-21 22:13:06.304687
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '86a501eb1189'
down_revision = '20260421_000016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create insight_events table for persistent event streaming
    op.create_table('insight_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('generation_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('group_index', sa.Integer(), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['generation_id'], ['insight_generations.id']),
        sa.PrimaryKeyConstraint('id'),
        sqlite_autoincrement=True
    )
    with op.batch_alter_table('insight_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_insight_events_generation_id'), ['generation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_insight_events_sequence'), ['sequence'], unique=False)

    # Add agent state and workspace fields to insight_generations
    with op.batch_alter_table('insight_generations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('workspace_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('session_state', sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('insight_generations', schema=None) as batch_op:
        batch_op.drop_column('session_state')
        batch_op.drop_column('workspace_json')

    with op.batch_alter_table('insight_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_insight_events_sequence'))
        batch_op.drop_index(batch_op.f('ix_insight_events_generation_id'))

    op.drop_table('insight_events')
