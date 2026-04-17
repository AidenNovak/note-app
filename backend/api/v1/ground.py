from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    GroundPost,
    GroundPostLike,
    InsightReport,
    Note,
    NoteLike,
    NoteTag,
    PostHide,
    PostReport,
    SharedNote,
    User,
    UserBlock,
)
from app.schemas import (
    BlockUserResponse,
    ExploreResponse,
    GroundFeedItem,
    GroundPostOut,
    HidePostResponse,
    NoteLikeResponse,
    NoteShareResponse,
    PostLikeResponse,
    PublicUserOut,
    ReportPostRequest,
    ReportPostResponse,
)
from app.auth.utils import get_current_user
from app.ground.moderation import (
    REPORT_REASONS,
    apply_visibility_filter,
    contains_banned_keyword,
    get_blocked_user_ids,
    get_hidden_post_ids,
)
from app.ground.recommendation import rank_posts
from app.notifications.triggers import notify_note_liked, notify_post_liked

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
    blocked = await get_blocked_user_ids(db, current_user.id)
    stmt = (
        select(SharedNote)
        .options(selectinload(SharedNote.note), selectinload(SharedNote.user), selectinload(SharedNote.likes))
        .order_by(SharedNote.shared_at.desc())
    )
    # Hide shares authored by blocked users (or users who blocked me)
    hostile = {uid for uid in blocked if uid != current_user.id}
    if hostile:
        stmt = stmt.where(SharedNote.user_id.notin_(hostile))
    stmt = stmt.offset(offset).limit(page_size)
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


@router.get("/explore", response_model=ExploreResponse)
async def explore(
    current_user: User = Depends(get_current_user),
):
    return {"trending": [], "recommended": [], "categories": []}


@router.post("/notes/{note_id}/share", response_model=NoteShareResponse)
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

    # Keyword banlist — auto-reject egregious content.
    matched = contains_banned_keyword(note.title, note.markdown_content)
    if matched:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "CONTENT_POLICY_VIOLATION",
                    "message": "This content isn't allowed on Ground.",
                }
            },
        )

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


@router.post("/notes/{note_id}/like", response_model=NoteLikeResponse)
async def like_note(
    note_id: str,
    background_tasks: BackgroundTasks,
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
    is_new = not existing.scalar_one_or_none()
    if is_new:
        db.add(NoteLike(id=str(uuid.uuid4()), shared_note_id=sn.id, user_id=current_user.id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            is_new = False

    if is_new and sn.user_id != current_user.id:
        background_tasks.add_task(
            notify_note_liked, sn.user_id,
            current_user.display_name or current_user.username or "Someone",
        )

    return {"note_id": note_id, "liked": True}


@router.delete("/notes/{note_id}/like", response_model=NoteLikeResponse)
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
    request: Request,
    response: Response,
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
        blocked = await get_blocked_user_ids(db, current_user.id)
        hidden = await get_hidden_post_ids(db, current_user.id)
        stmt = (
            select(GroundPost)
            .options(selectinload(GroundPost.user), selectinload(GroundPost.post_likes))
            .order_by(GroundPost.created_at.desc())
        )
        if post_type:
            stmt = stmt.where(GroundPost.post_type == post_type)
        stmt = apply_visibility_filter(stmt, current_user.id, blocked, hidden)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        posts = result.scalars().all()
        payload = [
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
        _not_modified = _apply_posts_etag(request, response, current_user.id, sort, post_type, page, page_size, payload)
        if _not_modified is not None:
            return _not_modified
        return payload

    # ── Recommended path: fetch 3x candidates, re-rank, paginate ──
    blocked = await get_blocked_user_ids(db, current_user.id)
    hidden = await get_hidden_post_ids(db, current_user.id)
    candidate_limit = page_size * 3
    stmt = (
        select(GroundPost)
        .options(selectinload(GroundPost.user), selectinload(GroundPost.post_likes))
        .order_by(GroundPost.created_at.desc())
    )
    if post_type:
        stmt = stmt.where(GroundPost.post_type == post_type)
    stmt = apply_visibility_filter(stmt, current_user.id, blocked, hidden).limit(candidate_limit)
    result = await db.execute(stmt)
    candidates = list(result.scalars().all())

    ranked = await rank_posts(db, current_user.id, candidates)

    # Paginate the ranked list
    start = (page - 1) * page_size
    page_items = ranked[start : start + page_size]

    payload = [
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
    _not_modified = _apply_posts_etag(request, response, current_user.id, sort, post_type, page, page_size, payload)
    if _not_modified is not None:
        return _not_modified
    return payload


def _apply_posts_etag(
    request: Request,
    response: Response,
    user_id: str,
    sort: str,
    post_type: str | None,
    page: int,
    page_size: int,
    payload: list[GroundPostOut],
) -> Response | None:
    """Compute a weak ETag for /ground/posts.

    Returns a bare 304 Response when If-None-Match matches (caller should
    return it directly). Otherwise sets ETag on the shared response and
    returns None so the caller returns the payload as usual.
    """
    fingerprint_parts = [
        f"u={user_id}",
        f"s={sort}",
        f"t={post_type or ''}",
        f"p={page}",
        f"ps={page_size}",
        f"n={len(payload)}",
    ]
    for p in payload:
        fingerprint_parts.append(
            f"{p.id}:{p.created_at.isoformat()}:{p.likes}:{int(p.liked_by_me)}"
        )
    digest = hashlib.md5("|".join(fingerprint_parts).encode("utf-8")).hexdigest()
    etag = f'W/"gp-{digest}"'
    client_etag = request.headers.get("if-none-match")
    if client_etag and client_etag == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"},
        )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return None


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
        raise HTTPException(status_code=404, detail={"error": {"code": "POST_NOT_FOUND", "message": "Post not found"}})

    # Moderation: owner can still view their own hidden post; otherwise mask.
    if p.user_id != current_user.id:
        if p.is_hidden:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "POST_REMOVED", "message": "This post has been removed"}},
            )
        blocked = await get_blocked_user_ids(db, current_user.id)
        if p.user_id in blocked:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "POST_UNAVAILABLE", "message": "This post is unavailable"}},
            )

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
    # Keyword banlist — auto-reject egregious content.
    matched = contains_banned_keyword(body.title, body.preview)
    if matched:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "CONTENT_POLICY_VIOLATION",
                    "message": "This content isn't allowed on Ground.",
                }
            },
        )
    # Validate ref exists
    if body.post_type == "note":
        result = await db.execute(select(Note).where(Note.id == body.ref_id, Note.user_id == current_user.id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail={"error": {"code": "NOTE_NOT_FOUND", "message": "Note not found"}})
    elif body.post_type == "insight":
        result = await db.execute(select(InsightReport).where(InsightReport.id == body.ref_id, InsightReport.user_id == current_user.id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail={"error": {"code": "INSIGHT_NOT_FOUND", "message": "Insight not found"}})
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


@router.post("/posts/{post_id}/like", response_model=PostLikeResponse)
async def like_post(
    post_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Like a ground post."""
    result = await db.execute(select(GroundPost).where(GroundPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail={"error": {"code": "POST_NOT_FOUND", "message": "Post not found"}})

    existing = await db.execute(
        select(GroundPostLike).where(GroundPostLike.post_id == post_id, GroundPostLike.user_id == current_user.id)
    )
    is_new = not existing.scalar_one_or_none()
    if is_new:
        db.add(GroundPostLike(id=str(uuid.uuid4()), post_id=post_id, user_id=current_user.id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            is_new = False

    if is_new and post.user_id != current_user.id:
        background_tasks.add_task(
            notify_post_liked, post.user_id,
            current_user.display_name or current_user.username or "Someone",
            post.title,
        )

    return {"post_id": post_id, "liked": True}


@router.delete("/posts/{post_id}/like", response_model=PostLikeResponse)
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


# ─────────────────────────────────────────────────────────────────────────
# Moderation (App Store Guideline 1.2 — UGC apps must provide users with
# a way to report content, block abusive users, and filter inappropriate
# content, with developer response within 24h.)
# ─────────────────────────────────────────────────────────────────────────


@router.post("/posts/{post_id}/report", response_model=ReportPostResponse)
async def report_post(
    post_id: str,
    body: ReportPostRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Report a Ground post for moderation review.

    The report is stored; moderators review via an admin surface and may
    set ``GroundPost.is_hidden = True`` to take the post down. Reports are
    unique per (post, reporter) — resubmitting silently no-ops.
    """
    if body.reason not in REPORT_REASONS:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_REASON", "message": "Unknown report reason"}},
        )

    result = await db.execute(select(GroundPost).where(GroundPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "POST_NOT_FOUND", "message": "Post not found"}},
        )
    if post.user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "CANNOT_REPORT_OWN", "message": "You cannot report your own post"}},
        )

    existing = await db.execute(
        select(PostReport).where(
            PostReport.post_id == post_id,
            PostReport.reporter_id == current_user.id,
        )
    )
    if existing.scalar_one_or_none() is None:
        report = PostReport(
            id=str(uuid.uuid4()),
            post_id=post_id,
            reporter_id=current_user.id,
            reason=body.reason,
            details=(body.details or None),
        )
        db.add(report)
        try:
            await db.commit()
        except IntegrityError:
            # racing duplicate — already reported, treat as success
            await db.rollback()

    return {"post_id": post_id, "reported": True}


@router.post("/users/{user_id}/block", response_model=BlockUserResponse)
async def block_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Block another user. Posts from blocked users disappear from feeds in both directions."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "CANNOT_BLOCK_SELF", "message": "You cannot block yourself"}},
        )

    target = await db.execute(select(User).where(User.id == user_id))
    if not target.scalar_one_or_none():
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "USER_NOT_FOUND", "message": "User not found"}},
        )

    existing = await db.execute(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_id == user_id,
        )
    )
    if not existing.scalar_one_or_none():
        db.add(
            UserBlock(
                id=str(uuid.uuid4()),
                blocker_id=current_user.id,
                blocked_id=user_id,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()

    return {"user_id": user_id, "blocked": True}


@router.delete("/users/{user_id}/block", response_model=BlockUserResponse)
async def unblock_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unblock a previously blocked user."""
    result = await db.execute(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_id == user_id,
        )
    )
    block = result.scalar_one_or_none()
    if block:
        await db.delete(block)
        await db.commit()
    return {"user_id": user_id, "blocked": False}


@router.get("/blocks", response_model=list[PublicUserOut])
async def list_blocks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List users I have blocked (for a Settings › Blocked Users screen)."""
    result = await db.execute(
        select(User)
        .join(UserBlock, UserBlock.blocked_id == User.id)
        .where(UserBlock.blocker_id == current_user.id)
        .order_by(UserBlock.created_at.desc())
    )
    users = result.scalars().all()
    return [
        PublicUserOut(id=u.id, username=u.username, avatar_url=u.avatar_url)
        for u in users
    ]


@router.post("/posts/{post_id}/hide", response_model=HidePostResponse)
async def hide_post(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-hide a post from my personal feed. Others still see it."""
    post = await db.execute(select(GroundPost).where(GroundPost.id == post_id))
    if not post.scalar_one_or_none():
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "POST_NOT_FOUND", "message": "Post not found"}},
        )
    existing = await db.execute(
        select(PostHide).where(PostHide.user_id == current_user.id, PostHide.post_id == post_id)
    )
    if not existing.scalar_one_or_none():
        db.add(
            PostHide(
                id=str(uuid.uuid4()),
                user_id=current_user.id,
                post_id=post_id,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
    return {"post_id": post_id, "hidden": True}


@router.delete("/posts/{post_id}/hide", response_model=HidePostResponse)
async def unhide_post(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Undo a personal hide."""
    result = await db.execute(
        select(PostHide).where(PostHide.user_id == current_user.id, PostHide.post_id == post_id)
    )
    hide = result.scalar_one_or_none()
    if hide:
        await db.delete(hide)
        await db.commit()
    return {"post_id": post_id, "hidden": False}
