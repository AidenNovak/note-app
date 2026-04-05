from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    File,
    MetadataSource,
    Note,
    NoteTag,
    NoteVersion,
    ProcessingTask,
    SourceType,
    TaskStatus,
    TaskType,
    User,
    VersionOrigin,
)
from app.note_collaboration import dumps_tags, normalize_tags, resolve_note_metadata
from app.schemas import NoteOut, TaskDetail, TaskListResponse, TaskOut
from app.storage import FileTooLargeError
from app.auth.utils import get_current_user

router = APIRouter(prefix="/notes", tags=["tasks"])

# ── Create note (with file or text) ─────────────────────

@router.post("", response_model=NoteOut, status_code=201)
async def create_note(
    background_tasks: BackgroundTasks,
    title: str | None = Form(None),
    folder_id: str | None = Form(None),
    tags: str | None = Form(None),  # comma-separated
    content: str | None = Form(None),
    file: UploadFile | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not content and not file:
        raise HTTPException(status_code=400, detail={"error": {"code": "MISSING_CONTENT", "message": "Provide 'content' or 'file'"}})

    note_id = str(uuid.uuid4())
    needs_processing = file is not None
    source_file_id = None
    source_type = SourceType.TEXT
    task_type = TaskType.TEXT_TO_MARKDOWN

    if file:
        # save file
        try:
            file_record = await _save_file(file, current_user.id, note_id, db)
        except FileTooLargeError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "FILE_TOO_LARGE",
                        "message": f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
                    }
                },
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": {"code": "STORAGE_UNAVAILABLE", "message": str(exc)}},
            ) from exc
        source_file_id = file_record.id
        mime = file_record.mime_type

        if mime.startswith("audio/"):
            source_type = SourceType.VOICE
            task_type = TaskType.VOICE_TO_TEXT
        elif mime.startswith("video/"):
            source_type = SourceType.VIDEO
            task_type = TaskType.VIDEO_TO_FRAMES
        else:
            source_type = SourceType.FILE
            task_type = TaskType.FILE_TO_MARKDOWN

    explicit_tags = tags.split(",") if tags else None
    if content:
        resolved = await resolve_note_metadata(
            content,
            explicit_title=title,
            explicit_tags=explicit_tags,
        )
        note_title = resolved.title
        title_source = resolved.title_source
        tag_values = resolved.tags
        tag_source = resolved.tag_source
        note_content = resolved.markdown_content
        initial_origin = VersionOrigin.HUMAN
    else:
        note_title = title or (file.filename if file else "Untitled Note")
        title_source = MetadataSource.HUMAN if title and title.strip() else MetadataSource.SYSTEM
        tag_values = normalize_tags(explicit_tags)
        tag_source = MetadataSource.HUMAN if tag_values else MetadataSource.NONE
        note_content = None
        initial_origin = VersionOrigin.SYSTEM

    note = Note(
        id=note_id,
        title=note_title,
        title_source=title_source,
        status=TaskStatus.PENDING if needs_processing else TaskStatus.COMPLETED,
        source_type=source_type,
        source_file_id=source_file_id,
        folder_id=folder_id,
        tag_source=tag_source,
        user_id=current_user.id,
    )
    db.add(note)

    if note_content:
        note.markdown_content = note_content

    for tag in tag_values:
        db.add(NoteTag(id=str(uuid.uuid4()), note_id=note_id, tag=tag))

    db.add(NoteVersion(
        id=str(uuid.uuid4()), note_id=note_id, version=1,
        version_origin=initial_origin,
        derived_from_version=None,
        title=note_title,
        title_source=title_source,
        tags_json=dumps_tags(tag_values),
        tag_source=tag_source,
        markdown_content=note_content,
        summary="Initial capture",
    ))

    task_id = None
    if needs_processing:
        task_id = str(uuid.uuid4())
        task = ProcessingTask(
            id=task_id, note_id=note_id, type=task_type,
            input_file_id=source_file_id,
        )
        db.add(task)
    await db.commit()

    if task_id is not None:
        background_tasks.add_task(_process_note, task_id, note_id, content, source_file_id, task_type)

    tags_result = await db.execute(select(NoteTag.tag).where(NoteTag.note_id == note_id))
    note_tags = [r[0] for r in tags_result.all()]

    return NoteOut(
        id=note_id, title=note_title, title_source=title_source.value,
        status=(TaskStatus.PENDING if needs_processing else TaskStatus.COMPLETED).value,
        folder_id=folder_id, tags=note_tags, tag_source=tag_source.value,
        created_at=note.created_at, updated_at=note.updated_at,
    )


# ── Task list & detail ─────────────────────────────

@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        select(ProcessingTask)
        .join(Note)
        .where(Note.user_id == current_user.id)
    )
    if status_filter:
        query = query.where(ProcessingTask.status == status_filter)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(ProcessingTask.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    tasks = result.scalars().all()

    return TaskListResponse(
        total=total, page=page, page_size=page_size,
        items=[
            TaskOut(
                id=t.id, note_id=t.note_id, type=t.type.value,
                status=t.status.value, progress=t.progress,
                created_at=t.created_at, updated_at=t.updated_at,
            ) for t in tasks
        ],
    )


# ── Separate router for /tasks/{id} (mounted at /tasks) ─

task_router = APIRouter(prefix="/tasks", tags=["tasks"])


@task_router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ProcessingTask)
        .join(Note)
        .where(ProcessingTask.id == task_id, Note.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail={"error": {"code": "TASK_NOT_FOUND", "message": "Task not found"}})

    return TaskDetail(
        id=task.id, note_id=task.note_id, type=task.type.value,
        status=task.status.value, progress=task.progress,
        error=task.error, input_file_id=task.input_file_id,
        created_at=task.created_at, updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


@task_router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ProcessingTask)
        .join(Note)
        .where(ProcessingTask.id == task_id, Note.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail={"error": {"code": "TASK_NOT_FOUND", "message": "Task not found"}})

    task.status = TaskStatus.PENDING
    task.progress = 0.0
    task.error = None
    await db.commit()

    background_tasks.add_task(_process_note, task.id, task.note_id, None, task.input_file_id, task.type)

    return {"id": task.id, "status": "pending", "message": "Task retried"}


# ── Helpers ─────────────────────────────────────────────

async def _save_file(upload: UploadFile, user_id: str, note_id: str, db: AsyncSession) -> File:
    file_id = str(uuid.uuid4())
    if not settings.EASYSTARTER_SERVER_URL:
        raise RuntimeError("EASYSTARTER_SERVER_URL is not configured")
    if not settings.STORAGE_MIGRATION_TOKEN:
        raise RuntimeError("STORAGE_MIGRATION_TOKEN is not configured")

    filename = upload.filename or "unknown"
    safe_filename = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename).strip("_") or "upload"
    key = f"attachments/{user_id}/{file_id}-{safe_filename}"

    try:
        upload.file.seek(0, os.SEEK_END)
        file_size = int(upload.file.tell())
        upload.file.seek(0)
    except Exception:
        file_size = 0

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size and file_size > max_bytes:
        raise FileTooLargeError

    content_type = upload.content_type or "application/octet-stream"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.EASYSTARTER_SERVER_URL.rstrip('/')}/api/storage/migrate",
            headers={"x-migration-token": settings.STORAGE_MIGRATION_TOKEN},
            data={"key": key, "contentType": content_type},
            files={"file": (filename, upload.file, content_type)},
        )

    if resp.status_code >= 400:
        raise RuntimeError(f"storage_migrate_failed:{resp.status_code}")
    try:
        payload = resp.json()
        migrated_size = int(payload.get("size", file_size)) if isinstance(payload, dict) else file_size
    except Exception:
        migrated_size = file_size

    db_file = File(
        id=file_id, filename=upload.filename or "unknown",
        mime_type=upload.content_type or "application/octet-stream",
        size=migrated_size, storage_path=key,
        user_id=user_id, note_id=note_id,
    )
    db.add(db_file)
    await db.flush()
    return db_file


async def _process_note(task_id: str, note_id: str, content: str | None, file_id: str | None, task_type: TaskType):
    """Background task: simulate AI processing pipeline.

    In production, this calls external AI APIs (Claude, Whisper, etc.)
    """
    import asyncio
    from app.database import async_session

    async with async_session() as db:
        # mark processing
        result = await db.execute(select(ProcessingTask).where(ProcessingTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return
        task.status = TaskStatus.PROCESSING
        task.progress = 0.1
        await db.commit()

        try:
            # TODO: replace with actual AI API calls
            # Simulate processing with a short delay
            await asyncio.sleep(2)

            # generate placeholder markdown
            note_result = await db.execute(select(Note).options(selectinload(Note.tags)).where(Note.id == note_id))
            note = note_result.scalar_one_or_none()
            if not note:
                return

            if content:
                markdown = f"# {note.title}\n\n{content}\n"
            else:
                markdown = f"# {note.title}\n\n> AI 处理完成（{task_type.value}）\n\n内容已转换为 Markdown 格式。\n"

            task.progress = 0.9
            await db.commit()

            current_tags = [tag.tag for tag in note.tags]
            resolved = await resolve_note_metadata(
                markdown,
                explicit_title=note.title if note.title_source == MetadataSource.HUMAN else None,
                explicit_tags=current_tags if note.tag_source == MetadataSource.HUMAN else None,
            )

            version_num = note.current_version + 1
            db.add(NoteVersion(
                id=str(uuid.uuid4()), note_id=note_id, version=version_num,
                version_origin=VersionOrigin.AI,
                derived_from_version=note.current_version,
                title=resolved.title,
                title_source=resolved.title_source,
                tags_json=dumps_tags(resolved.tags),
                tag_source=resolved.tag_source,
                markdown_content=resolved.markdown_content,
                summary=f"AI 转换完成（{task_type.value}）",
            ))

            note.title = resolved.title
            note.title_source = resolved.title_source
            note.markdown_content = resolved.markdown_content
            note.current_version = version_num
            note.status = TaskStatus.COMPLETED
            note.tag_source = resolved.tag_source
            await db.execute(NoteTag.__table__.delete().where(NoteTag.note_id == note_id))
            for tag in resolved.tags:
                db.add(NoteTag(id=str(uuid.uuid4()), note_id=note_id, tag=tag))

            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            await db.commit()
