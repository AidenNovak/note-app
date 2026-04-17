"""Add FK indexes on notes.folder_id and folders.parent_id

Queries like ``WHERE notes.folder_id = :id`` and ``WHERE folders.parent_id = :id``
were doing full table scans on Postgres. These composite-free btree indexes
make folder filtering and folder-tree traversal O(log n).

Revision ID: 20260417_000013
Revises: 20260715_000012
"""
from alembic import op


revision = "20260417_000013"
down_revision = "20260715_000012"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_notes_folder_id",
            "notes",
            ["folder_id"],
            unique=False,
            if_not_exists=True,
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_folders_parent_id",
            "folders",
            ["parent_id"],
            unique=False,
            if_not_exists=True,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_folders_parent_id", table_name="folders", if_exists=True, postgresql_concurrently=True)
        op.drop_index("ix_notes_folder_id", table_name="notes", if_exists=True, postgresql_concurrently=True)
