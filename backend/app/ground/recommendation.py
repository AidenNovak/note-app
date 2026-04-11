"""Ground feed recommendation: Jaccard tag similarity + time decay."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroundPost, NoteTag

# Weights
SIMILARITY_WEIGHT = 0.6
RECENCY_WEIGHT = 0.4
HALF_LIFE_DAYS = 7.0


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _recency_decay(created_at: datetime, half_life_days: float = HALF_LIFE_DAYS) -> float:
    now = datetime.utcnow()
    # Handle both naive and aware datetimes
    if created_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=None)
    age_days = max((now - created_at).total_seconds() / 86400, 0)
    return math.exp(-0.693 * age_days / half_life_days)


async def get_user_tags(db: AsyncSession, user_id: str) -> set[str]:
    """All unique tags from a user's notes."""
    from app.models import Note
    stmt = (
        select(NoteTag.tag)
        .join(Note, NoteTag.note_id == Note.id)
        .where(Note.user_id == user_id)
        .distinct()
    )
    result = await db.execute(stmt)
    return {row[0] for row in result.all()}


async def get_author_tags_batch(
    db: AsyncSession, author_ids: list[str],
) -> dict[str, set[str]]:
    """Batch-fetch tags for multiple authors (single query)."""
    from app.models import Note
    if not author_ids:
        return {}
    stmt = (
        select(Note.user_id, NoteTag.tag)
        .join(NoteTag, NoteTag.note_id == Note.id)
        .where(Note.user_id.in_(author_ids))
        .distinct()
    )
    result = await db.execute(stmt)
    tags_map: dict[str, set[str]] = {uid: set() for uid in author_ids}
    for user_id, tag in result.all():
        tags_map[user_id].add(tag)
    return tags_map


def _diversify(scored: list[tuple[GroundPost, float]]) -> list[tuple[GroundPost, float]]:
    """Greedy interleave: maximise author diversity in every window.

    Each step picks the highest-scored remaining post whose author
    hasn't appeared in the last `window` picks. Falls back gracefully
    when all remaining authors are recent.
    """
    if len(scored) <= 1:
        return scored

    remaining = list(scored)  # already sorted by score desc
    result: list[tuple[GroundPost, float]] = []
    recent_authors: list[str] = []  # sliding window of recent author ids
    n_authors = len({item[0].user_id for item in remaining})
    window = max(n_authors - 1, 1)  # avoid same author within this many slots

    while remaining:
        picked = None
        for i, item in enumerate(remaining):
            if item[0].user_id not in recent_authors:
                picked = remaining.pop(i)
                break
        if picked is None:
            # All remaining authors are in the recent window — relax and take best
            picked = remaining.pop(0)
        result.append(picked)
        recent_authors.append(picked[0].user_id)
        if len(recent_authors) > window:
            recent_authors.pop(0)

    return result


async def rank_posts(
    db: AsyncSession,
    user_id: str,
    posts: list[GroundPost],
) -> list[tuple[GroundPost, float]]:
    """Score and rank posts by tag similarity + recency.

    Returns (post, score) pairs sorted descending by score.
    """
    user_tags = await get_user_tags(db, user_id)
    if not user_tags:
        # No tags → pure recency
        return [(p, _recency_decay(p.created_at)) for p in posts]

    author_ids = list({p.user_id for p in posts if p.user_id != user_id})
    author_tags = await get_author_tags_batch(db, author_ids)

    scored: list[tuple[GroundPost, float]] = []
    for p in posts:
        a_tags = author_tags.get(p.user_id, set())
        sim = _jaccard(user_tags, a_tags)
        recency = _recency_decay(p.created_at)
        score = sim * SIMILARITY_WEIGHT + recency * RECENCY_WEIGHT
        scored.append((p, round(score, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return _diversify(scored)
