from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Note


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def workspace_path(generation_id: str) -> Path:
    backend_root = Path(__file__).resolve().parents[3]
    return (backend_root / settings.INSIGHTS_WORKSPACE_ROOT / generation_id).resolve()


async def fetch_note_context(db: AsyncSession, user_id: str) -> list[dict[str, object]]:
    result = await db.execute(
        select(Note)
        .options(selectinload(Note.tags))
        .where(Note.user_id == user_id)
        .order_by(Note.updated_at.desc())
        .limit(settings.INSIGHT_MAX_CONTEXT_NOTES)
    )

    return [
        {
            "id": note.id,
            "title": note.title,
            "tags": [tag.tag for tag in note.tags],
            "updated_at": note.updated_at.isoformat(),
            "current_version": note.current_version,
            "content": (note.markdown_content or "")[: settings.INSIGHT_MAX_NOTE_CHARS],
        }
        for note in result.scalars().all()
    ]


def write_workspace(generation_id: str, notes: list[dict[str, object]]) -> Path:
    target_path = workspace_path(generation_id)
    notes_path = target_path / "notes"
    notes_path.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "generation_id": generation_id,
        "generated_at": _utcnow().isoformat(),
        "note_count": len(notes),
        "notes": [],
    }

    for note in notes:
        relative_path = f"notes/{note['id']}.md"
        note_file = target_path / relative_path
        note_file.write_text(
            "\n".join(
                [
                    f"# {note['title']}",
                    "",
                    f"- note_id: {note['id']}",
                    f"- tags: {', '.join(note['tags']) if note['tags'] else 'none'}",
                    f"- updated_at: {note['updated_at']}",
                    f"- current_version: {note['current_version']}",
                    "",
                    "---",
                    "",
                    str(note["content"]).strip(),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        manifest["notes"].append(
            {
                "id": note["id"],
                "title": note["title"],
                "tags": note["tags"],
                "updated_at": note["updated_at"],
                "current_version": note["current_version"],
                "path": relative_path,
            }
        )

    (target_path / "context.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target_path
