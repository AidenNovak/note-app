from __future__ import annotations
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.utils import get_current_user
from app.database import get_db
from app.models import MetadataSource, Note, NoteTag, NoteVersion, TaskStatus, User, VersionOrigin
from app.note_collaboration import dumps_tags, generate_ai_note_version, loads_tags
from app.schemas import (
    AIVersionCreateRequest,
    VersionDetail,
    VersionListResponse,
    VersionOut,
)

router = APIRouter(prefix="/notes/{note_id}/versions", tags=["versions"])


def _version_out(version: NoteVersion) -> VersionOut:
    return VersionOut(
        version=version.version,
        version_origin=version.version_origin.value,
        derived_from_version=version.derived_from_version,
        title=version.title,
        title_source=version.title_source.value,
        tags=loads_tags(version.tags_json),
        tag_source=version.tag_source.value,
        summary=version.summary,
        created_at=version.created_at,
    )


def _version_detail(version: NoteVersion) -> VersionDetail:
    return VersionDetail(
        id=version.id,
        version=version.version,
        version_origin=version.version_origin.value,
        derived_from_version=version.derived_from_version,
        title=version.title,
        title_source=version.title_source.value,
        tags=loads_tags(version.tags_json),
        tag_source=version.tag_source.value,
        markdown_content=version.markdown_content,
        summary=version.summary,
        created_at=version.created_at,
    )


async def _get_note(db: AsyncSession, note_id: str, user_id: str) -> Note:
    note_result = await db.execute(
        select(Note).options(selectinload(Note.tags)).where(Note.id == note_id, Note.user_id == user_id)
    )
    note = note_result.scalar_one_or_none()
    if note is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}},
        )
    return note


async def _get_version(db: AsyncSession, note_id: str, version_number: int) -> NoteVersion:
    version_result = await db.execute(
        select(NoteVersion).where(NoteVersion.note_id == note_id, NoteVersion.version == version_number)
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "VERSION_NOT_FOUND", "message": "Version not found"}},
        )
    return version


async def _replace_note_tags(db: AsyncSession, note_id: str, tags: list[str]) -> None:
    await db.execute(NoteTag.__table__.delete().where(NoteTag.note_id == note_id))
    for tag in tags:
        db.add(NoteTag(id=str(uuid.uuid4()), note_id=note_id, tag=tag))


@router.get("", response_model=VersionListResponse)
async def list_versions(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_note(db, note_id, current_user.id)
    result = await db.execute(
        select(NoteVersion).where(NoteVersion.note_id == note_id).order_by(NoteVersion.version.desc())
    )
    versions = result.scalars().all()
    return VersionListResponse(note_id=note_id, versions=[_version_out(version) for version in versions])


@router.get("/{version}", response_model=VersionDetail)
async def get_version(
    note_id: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_note(db, note_id, current_user.id)
    return _version_detail(await _get_version(db, note_id, version))


@router.post("/ai", response_model=VersionDetail, status_code=status.HTTP_201_CREATED)
async def create_ai_version(
    note_id: str,
    body: AIVersionCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = await _get_note(db, note_id, current_user.id)
    base_version_number = body.source_version or note.current_version
    base_version = await _get_version(db, note_id, base_version_number)
    ai_payload = await generate_ai_note_version(
        title=base_version.title,
        markdown_content=base_version.markdown_content,
        tags=loads_tags(base_version.tags_json),
        instructions=body.instructions,
    )

    next_version = note.current_version + 1
    created_version = NoteVersion(
        id=str(uuid.uuid4()),
        note_id=note_id,
        version=next_version,
        version_origin=VersionOrigin.AI,
        derived_from_version=base_version.version,
        title=ai_payload.title,
        title_source=MetadataSource.AI,
        tags_json=dumps_tags(ai_payload.tags),
        tag_source=MetadataSource.AI if ai_payload.tags else MetadataSource.NONE,
        markdown_content=ai_payload.markdown_content,
        summary=ai_payload.summary,
    )
    db.add(created_version)

    note.title = ai_payload.title
    note.title_source = MetadataSource.AI
    note.markdown_content = ai_payload.markdown_content
    note.tag_source = MetadataSource.AI if ai_payload.tags else MetadataSource.NONE
    note.current_version = next_version
    note.status = TaskStatus.COMPLETED
    await _replace_note_tags(db, note_id, ai_payload.tags)

    await db.commit()
    await db.refresh(created_version)
    return _version_detail(created_version)


@router.post("/{version}/restore")
async def restore_version(
    note_id: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = await _get_note(db, note_id, current_user.id)
    source_version = await _get_version(db, note_id, version)

    next_version = note.current_version + 1
    restored_tags = loads_tags(source_version.tags_json)
    db.add(
        NoteVersion(
            id=str(uuid.uuid4()),
            note_id=note_id,
            version=next_version,
            version_origin=VersionOrigin.SYSTEM,
            derived_from_version=source_version.version,
            title=source_version.title,
            title_source=source_version.title_source,
            tags_json=source_version.tags_json,
            tag_source=source_version.tag_source,
            markdown_content=source_version.markdown_content,
            summary=f"Restored from version {version}",
        )
    )

    note.title = source_version.title
    note.title_source = source_version.title_source
    note.markdown_content = source_version.markdown_content
    note.tag_source = source_version.tag_source
    note.current_version = next_version
    note.status = TaskStatus.COMPLETED
    await _replace_note_tags(db, note_id, restored_tags)

    await db.commit()
    await db.refresh(note)

    return {
        "id": note.id,
        "title": note.title,
        "title_source": note.title_source.value,
        "status": note.status.value,
        "current_version": note.current_version,
        "markdown_content": note.markdown_content,
        "tags": restored_tags,
        "tag_source": note.tag_source.value,
    }
