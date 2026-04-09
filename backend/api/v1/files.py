from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import File, Note, User
from app.auth.utils import get_current_user
from app.schemas import FileDetail, FileListResponse, FileOut, FileReferenceListResponse, FileReferenceNoteOut, FileRegisterRequest
from app.storage import categorize_mime_type
from app.storage import read_stored_file

router = APIRouter(prefix="/files", tags=["files"])

LEGACY_PATH_PREFIXES = ("./", "data/", "tmp/", "var/")


def _storage_url(key: str, request: Request) -> str:
    base = settings.EASYSTARTER_SERVER_URL.rstrip("/") if settings.EASYSTARTER_SERVER_URL else ""
    if base:
        return f"{base}/api/storage/{key}"
    if key.startswith("http://") or key.startswith("https://"):
        return key
    return f"{str(request.base_url).rstrip('/')}/api/storage/{key}"


def _download_url(db_file: File, request: Request) -> str:
    storage_path = db_file.storage_path
    if storage_path.startswith("http://") or storage_path.startswith("https://"):
        return storage_path
    if not os.path.isabs(storage_path) and not storage_path.startswith(LEGACY_PATH_PREFIXES):
        return _storage_url(storage_path, request)
    return f"{str(request.base_url).rstrip('/')}/api/v1/files/{db_file.id}"


def _build_file_out(db_file: File, request: Request) -> FileOut:
    return FileOut(
        id=db_file.id,
        filename=db_file.filename,
        mime_type=db_file.mime_type,
        size=db_file.size,
        category=categorize_mime_type(db_file.mime_type),
        url=_download_url(db_file, request),
        note_id=db_file.note_id,
        created_at=db_file.created_at,
    )


def _file_reference(note: Note) -> FileReferenceNoteOut:
    return FileReferenceNoteOut(id=note.id, title=note.title, updated_at=note.updated_at)


def _category_clause(category: str):
    if category == "image":
        return File.mime_type.ilike("image/%")
    if category == "audio":
        return File.mime_type.ilike("audio/%")
    if category == "video":
        return File.mime_type.ilike("video/%")
    if category == "document":
        return or_(
            File.mime_type.ilike("text/%"),
            (
                File.mime_type.ilike("application/%")
                & (File.mime_type != "application/octet-stream")
            ),
        )
    if category == "other":
        return not_(
            or_(
                File.mime_type.ilike("image/%"),
                File.mime_type.ilike("audio/%"),
                File.mime_type.ilike("video/%"),
                File.mime_type.ilike("text/%"),
                File.mime_type.ilike("application/%"),
            )
        )
    raise HTTPException(
        status_code=400,
        detail={"error": {"code": "INVALID_FILE_CATEGORY", "message": f"Unsupported category '{category}'"}},
    )


@router.get("", response_model=FileListResponse)
async def list_files(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, min_length=1),
    category: str | None = Query(None),
    note_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(File).where(File.user_id == current_user.id)

    if q:
        query = query.where(File.filename.ilike(f"%{q}%"))
    if note_id:
        query = query.where(File.note_id == note_id)
    if category:
        query = query.where(_category_clause(category))

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(File.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    files = result.scalars().all()

    return FileListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_build_file_out(db_file, request) for db_file in files],
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_file(
    body: FileRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.note_id:
        note_result = await db.execute(select(Note).where(Note.id == body.note_id, Note.user_id == current_user.id))
        if not note_result.scalar_one_or_none():
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}},
            )

    file_id = str(uuid.uuid4())
    db_file = File(
        id=file_id,
        filename=body.filename,
        mime_type=body.content_type,
        size=body.size,
        storage_path=body.key,
        user_id=current_user.id,
        note_id=body.note_id,
    )
    db.add(db_file)
    await db.commit()
    await db.refresh(db_file)

    return {
        "id": db_file.id,
        "filename": db_file.filename,
        "mime_type": db_file.mime_type,
        "size": db_file.size,
        "category": categorize_mime_type(db_file.mime_type),
        "key": db_file.storage_path,
        "url": _storage_url(db_file.storage_path, request),
        "created_at": db_file.created_at,
    }


@router.get("/{file_id}/meta", response_model=FileDetail)
async def get_file_meta(
    file_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id, File.user_id == current_user.id))
    db_file = result.scalar_one_or_none()
    if not db_file:
        raise HTTPException(status_code=404, detail={"error": {"code": "FILE_NOT_FOUND", "message": "File not found"}})

    references: list[FileReferenceNoteOut] = []
    if db_file.note_id:
        note_result = await db.execute(
            select(Note).where(Note.id == db_file.note_id, Note.user_id == current_user.id)
        )
        note = note_result.scalar_one_or_none()
        if note:
            references.append(_file_reference(note))

    payload = _build_file_out(db_file, request)
    return FileDetail(**payload.model_dump(), references=references)


@router.get("/{file_id}/references", response_model=FileReferenceListResponse)
async def get_file_references(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id, File.user_id == current_user.id))
    db_file = result.scalar_one_or_none()
    if not db_file:
        raise HTTPException(status_code=404, detail={"error": {"code": "FILE_NOT_FOUND", "message": "File not found"}})

    references: list[FileReferenceNoteOut] = []
    if db_file.note_id:
        note_result = await db.execute(
            select(Note).where(Note.id == db_file.note_id, Note.user_id == current_user.id)
        )
        note = note_result.scalar_one_or_none()
        if note:
            references.append(_file_reference(note))

    return FileReferenceListResponse(file_id=file_id, references=references)


@router.get("/{file_id}")
async def get_file(
    file_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id, File.user_id == current_user.id))
    db_file = result.scalar_one_or_none()
    if not db_file:
        raise HTTPException(status_code=404, detail={"error": {"code": "FILE_NOT_FOUND", "message": "File not found"}})

    storage_path = db_file.storage_path
    if storage_path.startswith("http://") or storage_path.startswith("https://"):
        return RedirectResponse(url=_storage_url(storage_path, request), status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    if not os.path.isabs(storage_path) and not storage_path.startswith(LEGACY_PATH_PREFIXES):
        return RedirectResponse(url=_storage_url(storage_path, request), status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    try:
        content = await read_stored_file(storage_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "FILE_MISSING", "message": "File is missing from storage"}},
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "STORAGE_UNAVAILABLE", "message": str(exc)}},
        ) from exc

    return Response(
        content=content,
        media_type=db_file.mime_type,
        headers={"Content-Disposition": f'inline; filename="{db_file.filename}"'},
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id, File.user_id == current_user.id))
    db_file = result.scalar_one_or_none()
    if not db_file:
        raise HTTPException(status_code=404, detail={"error": {"code": "FILE_NOT_FOUND", "message": "File not found"}})

    await db.delete(db_file)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
