from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, distinct
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Note, NoteTag, User
from app.schemas import TagsAdd, TagListResponse
from app.auth.utils import get_current_user

router = APIRouter(prefix="/tags", tags=["tags"])


def _normalize_tags(raw_tags: list[str]) -> list[str]:
    return sorted({tag.strip().lower() for tag in raw_tags if tag.strip()})


@router.get("", response_model=TagListResponse)
async def list_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(distinct(NoteTag.tag))
        .join(Note)
        .where(Note.user_id == current_user.id)
        .order_by(NoteTag.tag)
    )
    return {"tags": [r[0] for r in result.all()]}


@router.post("/notes/{note_id}/tags", response_model=TagListResponse)
async def add_tags(
    note_id: str,
    body: TagsAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = await db.execute(select(Note).where(Note.id == note_id, Note.user_id == current_user.id))
    if not note.scalar_one_or_none():
        raise HTTPException(status_code=404, detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}})

    new_tags = _normalize_tags(body.tags)

    # Batch fetch existing tags to avoid N+1
    existing_result = await db.execute(
        select(NoteTag.tag).where(NoteTag.note_id == note_id, NoteTag.tag.in_(new_tags))
    )
    existing_tags = {r[0] for r in existing_result.all()}

    for tag in new_tags:
        if tag not in existing_tags:
            db.add(NoteTag(id=str(uuid.uuid4()), note_id=note_id, tag=tag))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()

    # Merge in-memory: existing (full set) ∪ newly inserted
    all_existing = await db.execute(select(NoteTag.tag).where(NoteTag.note_id == note_id))
    return {"tags": sorted(r[0] for r in all_existing.all())}


@router.delete("/notes/{note_id}/tags/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag(
    note_id: str,
    tag: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(NoteTag)
        .join(Note)
        .where(NoteTag.note_id == note_id, NoteTag.tag == tag, Note.user_id == current_user.id)
    )
    tag_record = result.scalar_one_or_none()
    if not tag_record:
        raise HTTPException(status_code=404, detail={"error": {"code": "TAG_NOT_FOUND", "message": "Tag not found on this note"}})

    await db.delete(tag_record)
    await db.commit()
