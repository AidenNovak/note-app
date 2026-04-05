from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260404_000005"
down_revision = "20260329_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notes") as batch_op:
        batch_op.add_column(
            sa.Column("title_source", sa.String(length=16), nullable=False, server_default="system")
        )
        batch_op.add_column(
            sa.Column("tag_source", sa.String(length=16), nullable=False, server_default="none")
        )

    with op.batch_alter_table("note_versions") as batch_op:
        batch_op.add_column(
            sa.Column("version_origin", sa.String(length=16), nullable=False, server_default="human")
        )
        batch_op.add_column(sa.Column("derived_from_version", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("title", sa.String(length=255), nullable=False, server_default="Untitled Note")
        )
        batch_op.add_column(
            sa.Column("title_source", sa.String(length=16), nullable=False, server_default="system")
        )
        batch_op.add_column(sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("tag_source", sa.String(length=16), nullable=False, server_default="none")
        )

    op.execute(
        """
        UPDATE notes
        SET
            title_source = CASE
                WHEN COALESCE(TRIM(title), '') = '' OR title = 'Untitled Note' THEN 'system'
                ELSE 'human'
            END,
            tag_source = CASE
                WHEN EXISTS (SELECT 1 FROM note_tags WHERE note_tags.note_id = notes.id) THEN 'human'
                ELSE 'none'
            END
        """
    )
    op.execute(
        """
        UPDATE note_versions
        SET
            version_origin = CASE
                WHEN lower(summary) LIKE '%ai%' THEN 'ai'
                WHEN lower(summary) LIKE 'restored from version%' THEN 'system'
                ELSE 'human'
            END,
            derived_from_version = CASE WHEN version > 1 THEN version - 1 ELSE NULL END,
            title = COALESCE((SELECT notes.title FROM notes WHERE notes.id = note_versions.note_id), 'Untitled Note'),
            title_source = COALESCE((SELECT notes.title_source FROM notes WHERE notes.id = note_versions.note_id), 'system'),
            tags_json = COALESCE(
                (
                    SELECT json_group_array(note_tags.tag)
                    FROM note_tags
                    WHERE note_tags.note_id = note_versions.note_id
                ),
                '[]'
            ),
            tag_source = COALESCE((SELECT notes.tag_source FROM notes WHERE notes.id = note_versions.note_id), 'none')
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("note_versions") as batch_op:
        batch_op.drop_column("tag_source")
        batch_op.drop_column("tags_json")
        batch_op.drop_column("title_source")
        batch_op.drop_column("title")
        batch_op.drop_column("derived_from_version")
        batch_op.drop_column("version_origin")

    with op.batch_alter_table("notes") as batch_op:
        batch_op.drop_column("tag_source")
        batch_op.drop_column("title_source")
