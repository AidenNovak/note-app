from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.utils import get_current_user
from app.config import settings
from app.database import get_db, async_session
from app.models import Folder, Note, NoteTag, NoteVersion, TaskStatus, User, VersionOrigin
from app.note_collaboration import dumps_tags, normalize_tags, resolve_note_metadata
from app.schemas import NoteCreate, NoteDetail, NoteListResponse, NoteOut, NoteUpdate
from app.storage import categorize_mime_type

router = APIRouter(prefix="/notes", tags=["notes"])


async def _background_embed(note_id: str, content: str, user_id: str) -> None:
    """Run embedding + similarity in a background task with its own DB session."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        from app.intelligence.embeddings import update_note_embedding, recompute_similarities
        async with async_session() as db:
            await update_note_embedding(db, note_id, content)
            await recompute_similarities(db, note_id, user_id)
    except Exception:
        logger.warning("Background embedding failed for note %s", note_id, exc_info=True)

NOTE_SORT_COLUMNS = {
    "created_at": Note.created_at,
    "updated_at": Note.updated_at,
    "title": Note.title,
    "status": Note.status,
}


def _tag_values(note: Note) -> list[str]:
    return sorted(tag.tag for tag in note.tags)


def _build_note_out(note: Note) -> NoteOut:
    return NoteOut(
        id=note.id,
        title=note.title,
        title_source=note.title_source.value,
        status=note.status.value,
        folder_id=note.folder_id,
        tags=_tag_values(note),
        tag_source=note.tag_source.value,
        source_type=note.source_type.value if note.source_type else None,
        attachment_count=len(note.attachments),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _build_note_version(
    *,
    note_id: str,
    version: int,
    summary: str,
    version_origin: VersionOrigin,
    derived_from_version: int | None,
    title: str,
    title_source: str,
    tags: list[str],
    tag_source: str,
    markdown_content: str | None,
) -> NoteVersion:
    return NoteVersion(
        id=str(uuid.uuid4()),
        note_id=note_id,
        version=version,
        version_origin=version_origin,
        derived_from_version=derived_from_version,
        title=title,
        title_source=title_source,
        tags_json=dumps_tags(tags),
        tag_source=tag_source,
        markdown_content=markdown_content,
        summary=summary,
    )


async def _validate_folder_access(db: AsyncSession, user_id: str, folder_id: str | None) -> None:
    if folder_id is None:
        return

    folder_result = await db.execute(select(Folder).where(Folder.id == folder_id, Folder.user_id == user_id))
    if not folder_result.scalar_one_or_none():
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "FOLDER_NOT_FOUND", "message": "Folder not found"}},
        )


async def _replace_note_tags(db: AsyncSession, note_id: str, tags: list[str]) -> None:
    await db.execute(NoteTag.__table__.delete().where(NoteTag.note_id == note_id))
    for tag in normalize_tags(tags):
        db.add(NoteTag(id=str(uuid.uuid4()), note_id=note_id, tag=tag))


@router.get("", response_model=NoteListResponse)
async def list_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    folder_id: str | None = None,
    tag: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    keyword: str | None = None,
    sort_by: str = Query("created_at"),
    order: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        select(Note)
        .options(selectinload(Note.tags), selectinload(Note.attachments))
        .where(Note.user_id == current_user.id)
    )

    if folder_id:
        query = query.where(Note.folder_id == folder_id)
    if tag:
        query = query.join(NoteTag).where(NoteTag.tag == tag)
    if status_filter:
        query = query.where(Note.status == status_filter)
    if keyword:
        query = query.where(
            or_(
                Note.title.ilike(f"%{keyword}%"),
                Note.markdown_content.ilike(f"%{keyword}%"),
            )
        )

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    sort_col = NOTE_SORT_COLUMNS.get(sort_by, Note.created_at)
    query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    notes = result.scalars().unique().all()

    return NoteListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_build_note_out(note) for note in notes],
    )


@router.get("/{note_id}", response_model=NoteDetail)
async def get_note(
    note_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Note)
        .options(selectinload(Note.attachments), selectinload(Note.tags))
        .where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}},
        )

    public_base = settings.EASYSTARTER_SERVER_URL.rstrip("/") if settings.EASYSTARTER_SERVER_URL else ""
    legacy_prefixes = ("./", "data/", "tmp/", "var/")
    backend_base = str(request.base_url).rstrip("/")
    attachments = [
        {
            "id": attachment.id,
            "type": categorize_mime_type(attachment.mime_type),
            "url": (
                f"{public_base}/api/storage/{attachment.storage_path}"
                if public_base
                and (not os.path.isabs(attachment.storage_path))
                and not attachment.storage_path.startswith(legacy_prefixes)
                else f"{backend_base}/api/v1/files/{attachment.id}"
            ),
            "filename": attachment.filename,
            "mime_type": attachment.mime_type,
            "size": attachment.size,
            "category": categorize_mime_type(attachment.mime_type),
        }
        for attachment in note.attachments
    ]

    return NoteDetail(
        id=note.id,
        title=note.title,
        title_source=note.title_source.value,
        status=note.status.value,
        markdown_content=note.markdown_content,
        attachments=attachments,
        folder_id=note.folder_id,
        tags=_tag_values(note),
        tag_source=note.tag_source.value,
        source_type=note.source_type.value if note.source_type else None,
        source_file_id=note.source_file_id,
        current_version=note.current_version,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note_id = str(uuid.uuid4())
    await _validate_folder_access(db, current_user.id, body.folder_id)

    resolved = await resolve_note_metadata(
        body.markdown_content,
        explicit_title=body.title,
        explicit_tags=body.tags,
        db=db,
        user_id=current_user.id,
    )

    note = Note(
        id=note_id,
        title=resolved.title,
        title_source=resolved.title_source,
        markdown_content=resolved.markdown_content,
        status=TaskStatus.COMPLETED,
        user_id=current_user.id,
        folder_id=body.folder_id,
        tag_source=resolved.tag_source,
        current_version=1,
    )
    db.add(note)
    await _replace_note_tags(db, note_id, resolved.tags)
    db.add(
        _build_note_version(
            note_id=note_id,
            version=1,
            summary="Initial capture",
            version_origin=VersionOrigin.HUMAN,
            derived_from_version=None,
            title=resolved.title,
            title_source=resolved.title_source,
            tags=resolved.tags,
            tag_source=resolved.tag_source,
            markdown_content=resolved.markdown_content,
        )
    )

    await db.commit()
    refreshed = await db.execute(
        select(Note)
        .options(selectinload(Note.tags), selectinload(Note.attachments))
        .where(Note.id == note_id, Note.user_id == current_user.id)
    )
    created_note = refreshed.scalar_one()

    # Async embedding + similarity
    if resolved.markdown_content:
        background_tasks.add_task(_background_embed, note_id, resolved.markdown_content, current_user.id)

    return _build_note_out(created_note)


@router.put("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str,
    body: NoteUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Note).options(selectinload(Note.tags)).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}},
        )

    if "folder_id" in body.model_fields_set:
        await _validate_folder_access(db, current_user.id, body.folder_id)
        note.folder_id = body.folder_id

    current_tags = _tag_values(note)
    content_changed = "markdown_content" in body.model_fields_set and body.markdown_content != note.markdown_content
    title_changed = "title" in body.model_fields_set and body.title is not None and body.title != note.title
    tags_changed = "tags" in body.model_fields_set and normalize_tags(body.tags) != current_tags

    if content_changed or title_changed or tags_changed:
        resolved = await resolve_note_metadata(
            body.markdown_content if "markdown_content" in body.model_fields_set else note.markdown_content,
            explicit_title=body.title if "title" in body.model_fields_set else None,
            explicit_tags=body.tags if "tags" in body.model_fields_set else None,
            fallback_title=note.title if not content_changed else None,
            fallback_title_source=note.title_source,
            fallback_tags=current_tags if not content_changed and "tags" not in body.model_fields_set else None,
            fallback_tag_source=note.tag_source,
            db=db,
            user_id=current_user.id,
        )

        previous_version = note.current_version
        next_version = previous_version + 1
        note.title = resolved.title
        note.title_source = resolved.title_source
        note.markdown_content = resolved.markdown_content
        note.tag_source = resolved.tag_source
        note.current_version = next_version
        note.status = TaskStatus.COMPLETED
        await _replace_note_tags(db, note.id, resolved.tags)
        db.add(
            _build_note_version(
                note_id=note.id,
                version=next_version,
                summary=body.version_summary or "Manual edit",
                version_origin=VersionOrigin.HUMAN,
                derived_from_version=previous_version,
                title=resolved.title,
                title_source=resolved.title_source,
                tags=resolved.tags,
                tag_source=resolved.tag_source,
                markdown_content=resolved.markdown_content,
            )
        )

    await db.commit()
    refreshed = await db.execute(
        select(Note)
        .options(selectinload(Note.tags), selectinload(Note.attachments))
        .where(Note.id == note.id, Note.user_id == current_user.id)
    )
    updated_note = refreshed.scalar_one()

    # Re-embed on content change
    if content_changed:
        final_content = note.markdown_content or ""
        if final_content:
            background_tasks.add_task(_background_embed, note.id, final_content, current_user.id)

    return _build_note_out(updated_note)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Note).where(Note.id == note_id, Note.user_id == current_user.id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}},
        )

    await db.delete(note)
    await db.commit()
