from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File as FormFile, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    AIStatus,
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

@router.post("/upload", response_model=NoteOut, status_code=201)
async def create_note(
    background_tasks: BackgroundTasks,
    title: str | None = Form(None),
    folder_id: str | None = Form(None),
    tags: str | None = Form(None),  # comma-separated
    content: str | None = Form(None),
    file: UploadFile | None = FormFile(None),
    files: list[UploadFile] | None = FormFile(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    uploads = [upload for upload in [file, *(files or [])] if upload is not None]
    if not content and not uploads:
        raise HTTPException(status_code=400, detail={"error": {"code": "MISSING_CONTENT", "message": "Provide 'content' or 'file'"}})

    note_id = str(uuid.uuid4())
    needs_processing = len(uploads) > 0
    source_file_id = None
    source_type = SourceType.TEXT
    task_type = TaskType.TEXT_TO_MARKDOWN
    saved_files: list[File] = []

    if uploads:
        try:
            for upload in uploads:
                saved_files.append(await _save_file(upload, current_user.id, note_id, db))
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

        file_record = saved_files[0]
        source_file_id = file_record.id
        mime = file_record.mime_type

        if mime.startswith("audio/"):
            source_type = SourceType.VOICE
            task_type = TaskType.VOICE_TO_TEXT
        elif mime.startswith("image/"):
            source_type = SourceType.IMAGE
            task_type = TaskType.IMAGE_TO_MARKDOWN
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
            skip_ai=True,
        )
        note_title = resolved.title
        title_source = resolved.title_source
        tag_values = resolved.tags
        tag_source = resolved.tag_source
        note_content = resolved.markdown_content
        initial_origin = VersionOrigin.HUMAN
        needs_ai_tags = resolved.needs_ai_tagging
    else:
        note_title = title or (uploads[0].filename if uploads else "Untitled Note")
        title_source = MetadataSource.HUMAN if title and title.strip() else MetadataSource.SYSTEM
        tag_values = normalize_tags(explicit_tags)
        tag_source = MetadataSource.HUMAN if tag_values else MetadataSource.NONE
        note_content = None
        initial_origin = VersionOrigin.SYSTEM
        needs_ai_tags = False

    note = Note(
        id=note_id,
        title=note_title,
        title_source=title_source,
        status=TaskStatus.PENDING if needs_processing else TaskStatus.COMPLETED,
        source_type=source_type,
        source_file_id=source_file_id,
        folder_id=folder_id,
        tag_source=tag_source,
        ai_status=AIStatus.PENDING if (needs_ai_tags and note_content) else AIStatus.IDLE,
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

    # Async AI tagging for text notes without explicit tags
    if needs_ai_tags and note_content:
        from app.database import async_session
        from api.v1.notes import _background_ai_tag
        background_tasks.add_task(
            _background_ai_tag, note_id, note_content,
            note_title, current_user.id,
        )

    tags_result = await db.execute(select(NoteTag.tag).where(NoteTag.note_id == note_id))
    note_tags = [r[0] for r in tags_result.all()]

    # Build content preview
    from api.v1.notes import _content_preview
    preview = _content_preview(note_content)

    return NoteOut(
        id=note_id, title=note_title, title_source=title_source.value,
        status=(TaskStatus.PENDING if needs_processing else TaskStatus.COMPLETED).value,
        folder_id=folder_id, tags=note_tags, tag_source=tag_source.value,
        ai_status=note.ai_status.value,
        source_type=source_type.value if source_type else None,
        attachment_count=len(saved_files),
        content_preview=preview,
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


# ── Task detail & retry (also under /notes prefix) ─

@router.get("/tasks/{task_id}", response_model=TaskDetail)
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


@router.post("/tasks/{task_id}/retry")
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

    note_result = await db.execute(select(Note).where(Note.id == task.note_id, Note.user_id == current_user.id))
    note = note_result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}})

    task.status = TaskStatus.PENDING
    task.progress = 0.0
    task.error = None
    task.completed_at = None
    note.status = TaskStatus.PENDING
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
    except (OSError, AttributeError):
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
    except (ValueError, KeyError, TypeError):
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
    """Background task: process uploaded files via AI APIs (Whisper, Vision, file extraction)."""
    from app.database import async_session

    async with async_session() as db:
        note: Note | None = None
        result = await db.execute(select(ProcessingTask).where(ProcessingTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return

        try:
            note_result = await db.execute(select(Note).options(selectinload(Note.tags)).where(Note.id == note_id))
            note = note_result.scalar_one_or_none()
            if not note:
                task.status = TaskStatus.FAILED
                task.error = "Note not found"
                await db.commit()
                return

            task.status = TaskStatus.PROCESSING
            task.progress = 0.1
            note.status = TaskStatus.PROCESSING
            await db.commit()

            markdown = ""

            if task_type == TaskType.VOICE_TO_TEXT:
                markdown = await _voice_to_text(file_id, db)
            elif task_type == TaskType.IMAGE_TO_MARKDOWN:
                markdown = await _image_to_markdown(file_id, db)
            elif task_type == TaskType.FILE_TO_MARKDOWN:
                markdown = await _file_to_markdown(file_id, db)
            elif content:
                markdown = f"# {note.title}\n\n{content}\n"
            else:
                markdown = f"# {note.title}\n\n> Processed ({task_type.value})\n"

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
                summary=f"AI processing complete ({task_type.value})",
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
            import logging
            logging.getLogger(__name__).error(
                "Background processing failed for note %s task %s: %s",
                note_id, task_id, e, exc_info=True,
            )
            task.status = TaskStatus.FAILED
            task.error = str(e)
            if note is not None:
                note.status = TaskStatus.FAILED
            await db.commit()


async def _voice_to_text(file_id: str | None, db: AsyncSession) -> str:
    """Transcribe audio via OpenAI Whisper API."""
    if not file_id:
        return "> No audio file provided.\n"

    file_result = await db.execute(select(File).where(File.id == file_id))
    file_record = file_result.scalar_one_or_none()
    if not file_record:
        return "> Audio file not found.\n"

    storage_url = _get_file_url(file_record.storage_path)

    openai_key = os.environ.get("OPENAI_API_KEY", settings.OPENAI_API_KEY)
    if not openai_key:
        return f"> [Audio transcription pending — no OPENAI_API_KEY configured]\n> File: {file_record.filename}\n"

    async with httpx.AsyncClient(timeout=120.0) as client:
        file_resp = await client.get(storage_url)
        if file_resp.status_code >= 400:
            return "> Could not download audio file for transcription.\n"

    import openai
    oai = openai.AsyncOpenAI(api_key=openai_key)
    import io
    audio_file = io.BytesIO(file_resp.content)
    audio_file.name = file_record.filename

    transcription = await oai.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
    )

    text = transcription.text
    return f"# Voice Note\n\n{text}\n" if text else "> Empty transcription result.\n"


async def _image_to_markdown(file_id: str | None, db: AsyncSession) -> str:
    """Describe image content via OpenRouter vision model."""
    if not file_id:
        return "> No image file provided.\n"

    file_result = await db.execute(select(File).where(File.id == file_id))
    file_record = file_result.scalar_one_or_none()
    if not file_record:
        return "> Image file not found.\n"

    storage_url = _get_file_url(file_record.storage_path)

    if not settings.OPENROUTER_API_KEY:
        return f"> [Image description pending — no OPENROUTER_API_KEY configured]\n> File: {file_record.filename}\n"

    async with httpx.AsyncClient(timeout=60.0) as client:
        img_resp = await client.get(storage_url)
        if img_resp.status_code >= 400:
            return f"> Could not download image for analysis.\n"

    import base64
    image_data = base64.b64encode(img_resp.content).decode("utf-8")
    media_type = file_record.mime_type if file_record.mime_type.startswith("image/") else "image/jpeg"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.AI_MODEL,
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}},
                        {"type": "text", "text": "Describe this image in detail. Write the description as clean markdown suitable for a note. Include any text visible in the image."},
                    ],
                }],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    description = data["choices"][0]["message"]["content"] if data.get("choices") else ""
    return f"# Image Note\n\n{description}\n"


async def _file_to_markdown(file_id: str | None, db: AsyncSession) -> str:
    """Extract text from PDF/DOCX or describe via Claude."""
    if not file_id:
        return "> No file provided.\n"

    file_result = await db.execute(select(File).where(File.id == file_id))
    file_record = file_result.scalar_one_or_none()
    if not file_record:
        return "> File not found.\n"

    storage_url = _get_file_url(file_record.storage_path)

    async with httpx.AsyncClient(timeout=60.0) as client:
        file_resp = await client.get(storage_url)
        if file_resp.status_code >= 400:
            return f"> Could not download file for extraction.\n"

    file_bytes = file_resp.content
    mime = file_record.mime_type

    if mime == "application/pdf":
        return _extract_pdf(file_bytes, file_record.filename)
    elif mime in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"):
        return _extract_docx(file_bytes, file_record.filename)
    else:
        return f"# {file_record.filename}\n\n> File type `{mime}` — content extraction not yet supported.\n"


def _extract_pdf(data: bytes, filename: str) -> str:
    """Extract text from PDF using pdfplumber."""
    try:
        import io
        import pdfplumber
        pages_text = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
        if pages_text:
            return f"# {filename}\n\n" + "\n\n---\n\n".join(pages_text) + "\n"
        return f"# {filename}\n\n> PDF contained no extractable text.\n"
    except Exception as e:
        return f"# {filename}\n\n> PDF extraction failed: {e}\n"


def _extract_docx(data: bytes, filename: str) -> str:
    """Extract text from DOCX using python-docx."""
    try:
        import io
        import docx
        doc = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if paragraphs:
            return f"# {filename}\n\n" + "\n\n".join(paragraphs) + "\n"
        return f"# {filename}\n\n> Document contained no text.\n"
    except Exception as e:
        return f"# {filename}\n\n> DOCX extraction failed: {e}\n"


def _get_file_url(storage_path: str) -> str:
    """Build a public URL for a stored file."""
    if not settings.EASYSTARTER_SERVER_URL:
        return ""
    return f"{settings.EASYSTARTER_SERVER_URL.rstrip('/')}/api/storage/{storage_path}"
