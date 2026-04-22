"""Clear note embeddings for Cloudflare Workers AI migration.

Embedding model changed from openai/text-embedding-3-small (1536 dims) to
@cf/baai/bge-m3 (multilingual, different dimension). Old vectors are
incompatible — clear them so notes are re-embedded on next update, or run
the backfill script:

  python backend/scripts/backfill_embeddings.py

Revision ID: 20260720_000017
Revises: 86a501eb1189
Create Date: 2026-07-20
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic
revision = "20260720_000017"
down_revision = "86a501eb1189"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Clearing similarities first (FK constraint on note_id).
    op.execute("DELETE FROM note_similarities")
    op.execute("DELETE FROM note_embeddings")


def downgrade() -> None:
    # Cannot restore embeddings — must backfill again after rollback.
    pass
