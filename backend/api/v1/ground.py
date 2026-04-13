from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import GroundPost, GroundPostLike, InsightReport, Note, NoteLike, NoteTag, SharedNote, User
from app.schemas import GroundFeedItem, GroundPostOut, PublicUserOut
from app.auth.utils import get_current_user
from app.ground.recommendation import rank_posts

router = APIRouter(prefix="/ground", tags=["ground"])


@router.get("/feed", response_model=list[GroundFeedItem])
async def get_feed(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Public feed — only notes that have been explicitly shared."""
    offset = (page - 1) * page_size
    stmt = (
        select(SharedNote)
        .options(selectinload(SharedNote.note), selectinload(SharedNote.user), selectinload(SharedNote.likes))
        .order_by(SharedNote.shared_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    shared_notes = result.scalars().all()

    items = []
    for sn in shared_notes:
        if not sn.note:
            continue
        like_count = len(sn.likes)
        liked_by_me = any(lk.user_id == current_user.id for lk in sn.likes)
        items.append(GroundFeedItem(
            id=sn.id,
            note_id=sn.note_id,
            author=PublicUserOut(id=sn.user.id, username=sn.user.username, avatar_url=sn.user.avatar_url),
            title=sn.note.title,
            preview=(sn.note.markdown_content or "")[:120],
            likes=like_count,
            liked_by_me=liked_by_me,
            shared_at=sn.shared_at,
        ))
    return items


@router.get("/explore")
async def explore(
    current_user: User = Depends(get_current_user),
):
    return {"trending": [], "recommended": [], "categories": []}


@router.post("/notes/{note_id}/share")
async def share_note(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Share a note to Ground feed via the unified posts system."""
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}})

    # Check if already shared as a ground post
    existing = await db.execute(
        select(GroundPost).where(GroundPost.ref_id == note_id, GroundPost.post_type == "note", GroundPost.user_id == current_user.id)
    )
    if existing.scalar_one_or_none():
        return {"note_id": note_id, "shared": True}

    post = GroundPost(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        post_type="note",
        ref_id=note_id,
        title=note.title or "Untitled Note",
        preview=(note.markdown_content or "")[:200],
    )
    db.add(post)
    await db.commit()

    return {"note_id": note_id, "shared": True}


@router.post("/notes/{note_id}/like")
async def like_note(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(SharedNote).where(SharedNote.note_id == note_id))
    sn = result.scalar_one_or_none()
    if not sn:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_SHARED", "message": "Note is not shared"}})

    existing = await db.execute(
        select(NoteLike).where(NoteLike.shared_note_id == sn.id, NoteLike.user_id == current_user.id)
    )
    if not existing.scalar_one_or_none():
        db.add(NoteLike(id=str(uuid.uuid4()), shared_note_id=sn.id, user_id=current_user.id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()

    return {"note_id": note_id, "liked": True}


@router.delete("/notes/{note_id}/like")
async def unlike_note(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(SharedNote).where(SharedNote.note_id == note_id))
    sn = result.scalar_one_or_none()
    if sn:
        like_result = await db.execute(
            select(NoteLike).where(NoteLike.shared_note_id == sn.id, NoteLike.user_id == current_user.id)
        )
        like = like_result.scalar_one_or_none()
        if like:
            await db.delete(like)
            await db.commit()

    return {"note_id": note_id, "liked": False}


# ── New Ground Posts (mind graph + insight sharing) ──────────────────────


class SharePostRequest(BaseModel):
    post_type: str = Field(pattern="^(note|mind_graph|insight)$")
    ref_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=255)
    preview: str = Field(default="", max_length=500)
    extra_json: str | None = None


@router.get("/posts", response_model=list[GroundPostOut])
async def get_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    post_type: str | None = None,
    sort: str = Query("recommended", pattern="^(recommended|recent)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unified feed of all ground posts (notes, mind graphs, insights).

    sort=recommended (default): Jaccard tag similarity + time decay.
    sort=recent: pure reverse-chronological.
    """
    if sort == "recent":
        # ── Pure time-sorted path (original behaviour) ──
        stmt = (
            select(GroundPost)
            .options(selectinload(GroundPost.user), selectinload(GroundPost.post_likes))
            .order_by(GroundPost.created_at.desc())
        )
        if post_type:
            stmt = stmt.where(GroundPost.post_type == post_type)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        posts = result.scalars().all()
        return [
            GroundPostOut(
                id=p.id, post_type=p.post_type, ref_id=p.ref_id,
                author=PublicUserOut(id=p.user.id, username=p.user.username, avatar_url=p.user.avatar_url),
                title=p.title, preview=p.preview, extra_json=p.extra_json,
                likes=len(p.post_likes),
                liked_by_me=any(lk.user_id == current_user.id for lk in p.post_likes),
                created_at=p.created_at,
            )
            for p in posts
        ]

    # ── Recommended path: fetch 3x candidates, re-rank, paginate ──
    candidate_limit = page_size * 3
    stmt = (
        select(GroundPost)
        .options(selectinload(GroundPost.user), selectinload(GroundPost.post_likes))
        .order_by(GroundPost.created_at.desc())
        .limit(candidate_limit)
    )
    if post_type:
        stmt = stmt.where(GroundPost.post_type == post_type)
    result = await db.execute(stmt)
    candidates = list(result.scalars().all())

    ranked = await rank_posts(db, current_user.id, candidates)

    # Paginate the ranked list
    start = (page - 1) * page_size
    page_items = ranked[start : start + page_size]

    return [
        GroundPostOut(
            id=p.id, post_type=p.post_type, ref_id=p.ref_id,
            author=PublicUserOut(id=p.user.id, username=p.user.username, avatar_url=p.user.avatar_url),
            title=p.title, preview=p.preview, extra_json=p.extra_json,
            likes=len(p.post_likes),
            liked_by_me=any(lk.user_id == current_user.id for lk in p.post_likes),
            relevance_score=score,
            created_at=p.created_at,
        )
        for p, score in page_items
    ]


@router.get("/posts/{post_id}")
async def get_post(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(GroundPost)
        .options(selectinload(GroundPost.user), selectinload(GroundPost.post_likes))
        .where(GroundPost.id == post_id)
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Post not found")

    # Fetch referenced content
    content = None
    tags = []
    if p.post_type == "note" and p.ref_id:
        note_result = await db.execute(select(Note).where(Note.id == p.ref_id))
        note = note_result.scalar_one_or_none()
        if note:
            content = note.markdown_content
            tag_result = await db.execute(
                select(NoteTag.tag).where(NoteTag.note_id == note.id)
            )
            tags = [r[0] for r in tag_result.all()]
    elif p.post_type == "insight" and p.ref_id:
        report_result = await db.execute(select(InsightReport).where(InsightReport.id == p.ref_id))
        report = report_result.scalar_one_or_none()
        if report:
            content = report.report_markdown

    out = {
        "id": p.id,
        "post_type": p.post_type,
        "ref_id": p.ref_id,
        "author": {"id": p.user.id, "username": p.user.username},
        "title": p.title,
        "preview": p.preview,
        "extra_json": p.extra_json,
        "likes": len(p.post_likes),
        "liked_by_me": any(lk.user_id == current_user.id for lk in p.post_likes),
        "created_at": p.created_at.isoformat(),
        "content": content,
        "tags": tags,
    }
    return out


@router.post("/posts", response_model=GroundPostOut)
async def create_post(
    body: SharePostRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Share a mind graph or insight to the Ground feed."""
    # Validate ref exists
    if body.post_type == "note":
        result = await db.execute(select(Note).where(Note.id == body.ref_id, Note.user_id == current_user.id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Note not found")
    elif body.post_type == "insight":
        result = await db.execute(select(InsightReport).where(InsightReport.id == body.ref_id, InsightReport.user_id == current_user.id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Insight not found")
    # mind_graph: ref_id is user_id, no validation needed

    post = GroundPost(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        post_type=body.post_type,
        ref_id=body.ref_id,
        title=body.title,
        preview=body.preview[:500],
        extra_json=body.extra_json,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    return GroundPostOut(
        id=post.id,
        post_type=post.post_type,
        ref_id=post.ref_id,
        author=PublicUserOut(id=current_user.id, username=current_user.username, avatar_url=current_user.avatar_url),
        title=post.title,
        preview=post.preview,
        extra_json=post.extra_json,
        likes=0,
        liked_by_me=False,
        created_at=post.created_at,
    )


@router.post("/posts/{post_id}/like")
async def like_post(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Like a ground post."""
    result = await db.execute(select(GroundPost).where(GroundPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = await db.execute(
        select(GroundPostLike).where(GroundPostLike.post_id == post_id, GroundPostLike.user_id == current_user.id)
    )
    if not existing.scalar_one_or_none():
        db.add(GroundPostLike(id=str(uuid.uuid4()), post_id=post_id, user_id=current_user.id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()

    return {"post_id": post_id, "liked": True}


@router.delete("/posts/{post_id}/like")
async def unlike_post(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unlike a ground post."""
    result = await db.execute(
        select(GroundPostLike).where(GroundPostLike.post_id == post_id, GroundPostLike.user_id == current_user.id)
    )
    like = result.scalar_one_or_none()
    if like:
        await db.delete(like)
        await db.commit()

    return {"post_id": post_id, "liked": False}
