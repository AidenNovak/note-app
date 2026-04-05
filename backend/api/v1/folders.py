from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Folder, Note, User
from app.schemas import FolderCreate, FolderOut, FolderUpdate
from app.auth.utils import get_current_user

router = APIRouter(prefix="/folders", tags=["folders"])


def _build_tree(folders: list[Folder]) -> list[FolderOut]:
    """Build tree structure from flat folder list."""
    by_id: dict[str, FolderOut] = {}
    roots: list[FolderOut] = []

    for f in folders:
        by_id[f.id] = FolderOut(
            id=f.id, name=f.name, parent_id=f.parent_id,
            created_at=f.created_at, updated_at=f.updated_at, children=[],
        )

    for f in folders:
        node = by_id[f.id]
        if f.parent_id and f.parent_id in by_id:
            by_id[f.parent_id].children.append(node)
        else:
            roots.append(node)

    return roots


async def _validate_parent_folder(
    db: AsyncSession,
    current_user: User,
    parent_id: str | None,
    folder_id: str | None = None,
) -> None:
    if parent_id is None:
        return
    if folder_id and parent_id == folder_id:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_PARENT", "message": "Folder cannot be its own parent"}},
        )

    parent_result = await db.execute(
        select(Folder).where(Folder.id == parent_id, Folder.user_id == current_user.id)
    )
    parent = parent_result.scalar_one_or_none()
    if not parent:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "FOLDER_NOT_FOUND", "message": "Parent folder not found"}},
        )

    cursor = parent
    while folder_id and cursor.parent_id:
        if cursor.parent_id == folder_id:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": "FOLDER_CYCLE", "message": "Folder hierarchy cannot contain cycles"}},
            )
        cursor_result = await db.execute(
            select(Folder).where(Folder.id == cursor.parent_id, Folder.user_id == current_user.id)
        )
        cursor = cursor_result.scalar_one_or_none()
        if not cursor:
            break


@router.get("", response_model=list[FolderOut])
async def list_folders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Folder).where(Folder.user_id == current_user.id).order_by(Folder.name)
    )
    return _build_tree(result.scalars().all())


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _validate_parent_folder(db, current_user, body.parent_id)

    folder = Folder(id=str(uuid.uuid4()), name=body.name, parent_id=body.parent_id, user_id=current_user.id)
    db.add(folder)
    await db.commit()
    await db.refresh(folder)

    return FolderOut(id=folder.id, name=folder.name, parent_id=folder.parent_id,
                     created_at=folder.created_at, updated_at=folder.updated_at, children=[])


@router.put("/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: str,
    body: FolderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Folder).where(Folder.id == folder_id, Folder.user_id == current_user.id))
    folder = result.scalar_one_or_none()
    if not folder:
        raise HTTPException(status_code=404, detail={"error": {"code": "FOLDER_NOT_FOUND", "message": "Folder not found"}})

    if "name" in body.model_fields_set and body.name is not None:
        folder.name = body.name
    if "parent_id" in body.model_fields_set:
        await _validate_parent_folder(db, current_user, body.parent_id, folder_id=folder_id)
        folder.parent_id = body.parent_id

    await db.commit()
    await db.refresh(folder)
    return FolderOut(id=folder.id, name=folder.name, parent_id=folder.parent_id,
                     created_at=folder.created_at, updated_at=folder.updated_at, children=[])


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Folder).where(Folder.id == folder_id, Folder.user_id == current_user.id))
    folder = result.scalar_one_or_none()
    if not folder:
        raise HTTPException(status_code=404, detail={"error": {"code": "FOLDER_NOT_FOUND", "message": "Folder not found"}})

    child_count = (
        await db.execute(
            select(func.count()).select_from(Folder).where(Folder.parent_id == folder_id, Folder.user_id == current_user.id)
        )
    ).scalar_one()
    note_count = (
        await db.execute(
            select(func.count()).select_from(Note).where(Note.folder_id == folder_id, Note.user_id == current_user.id)
        )
    ).scalar_one()
    if child_count or note_count:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "FOLDER_NOT_EMPTY", "message": "Folder must be empty before deletion"}},
        )

    await db.delete(folder)
    await db.commit()
