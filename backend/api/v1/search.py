from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Note, NoteTag, User
from app.schemas import SearchResponse, SearchResultItem, SuggestResponse
from app.auth.utils import get_current_user

router = APIRouter(prefix="/search", tags=["search"])


def _parse_datetime_param(value: str | None, field_name: str) -> datetime | None:
    if value is None:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_DATE_FILTER", "message": f"Invalid {field_name} value"}},
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    type: str = Query("all"),
    folder_id: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if type == "file":
        return SearchResponse(total=0, page=page, page_size=page_size, items=[])
    if type not in {"all", "note"}:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "UNSUPPORTED_SEARCH_TYPE", "message": f"Unsupported search type '{type}'"}},
        )

    query = select(Note).where(Note.user_id == current_user.id)
    query = query.where(
        or_(
            Note.title.ilike(f"%{q}%"),
            Note.markdown_content.ilike(f"%{q}%"),
        )
    )

    if folder_id:
        query = query.where(Note.folder_id == folder_id)
    if tag:
        query = query.join(NoteTag).where(NoteTag.tag == tag)
    if created_from := _parse_datetime_param(date_from, "date_from"):
        query = query.where(Note.created_at >= created_from)
    if created_to := _parse_datetime_param(date_to, "date_to"):
        query = query.where(Note.created_at <= created_to)

    # count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Note.updated_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    notes = result.scalars().all()

    items = []
    for n in notes:
        # extract highlight snippet
        content = n.markdown_content or ""
        idx = content.lower().find(q.lower())
        if idx >= 0:
            start = max(0, idx - 30)
            end = min(len(content), idx + len(q) + 30)
            highlight = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
        else:
            highlight = (content[:80] + "...") if len(content) > 80 else content

        items.append(SearchResultItem(
            id=n.id, type="note", title=n.title,
            highlight=highlight, created_at=n.created_at,
        ))

    return SearchResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/suggest", response_model=SuggestResponse)
async def suggest(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Note.title)
        .where(Note.user_id == current_user.id, Note.title.ilike(f"{q}%"))
        .order_by(Note.updated_at.desc())
        .limit(limit)
    )
    return SuggestResponse(suggestions=[r[0] for r in result.all()])
