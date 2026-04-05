from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import File, Note, User
from app.auth.utils import get_current_user
from app.schemas import FileRegisterRequest
from app.storage import read_stored_file

router = APIRouter(prefix="/files", tags=["files"])

LEGACY_PATH_PREFIXES = ("./", "data/", "tmp/", "var/")


def _storage_url(key: str) -> str:
    base = settings.EASYSTARTER_SERVER_URL.rstrip("/") if settings.EASYSTARTER_SERVER_URL else ""
    if base:
        return f"{base}/api/storage/{key}"
    if key.startswith("http://") or key.startswith("https://"):
        return key
    return f"/api/storage/{key}"


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_file(
    body: FileRegisterRequest,
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
        "key": db_file.storage_path,
        "url": _storage_url(db_file.storage_path),
        "created_at": db_file.created_at,
    }


@router.get("/{file_id}")
async def get_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id, File.user_id == current_user.id))
    db_file = result.scalar_one_or_none()
    if not db_file:
        raise HTTPException(status_code=404, detail={"error": {"code": "FILE_NOT_FOUND", "message": "File not found"}})

    storage_path = db_file.storage_path
    if storage_path.startswith("http://") or storage_path.startswith("https://"):
        return RedirectResponse(url=_storage_url(storage_path), status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    if not os.path.isabs(storage_path) and not storage_path.startswith(LEGACY_PATH_PREFIXES):
        return RedirectResponse(url=_storage_url(storage_path), status_code=status.HTTP_307_TEMPORARY_REDIRECT)

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
