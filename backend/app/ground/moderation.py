"""Ground moderation helpers: keyword banlist, block/hide query filters.

Fulfils App Store Guideline 1.2 UGC requirements:
  * users can report objectionable content (via POST /ground/posts/{id}/report)
  * users can block other users (via POST /ground/users/{id}/block)
  * objectionable content is filtered from feeds (keyword banlist + admin takedown)
  * developer can remove offending content within 24h (admin sets is_hidden=True)
"""
from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroundPost, PostHide, UserBlock


# A conservative, broad keyword banlist for hard auto-reject.
# Keep this short and unambiguous — anything nuanced should go through
# user reports + human review, not an auto-reject.
_BANNED_SUBSTRINGS: tuple[str, ...] = (
    # Child sexual abuse material — absolute prohibition
    "child porn",
    "cp porn",
    "underage sex",
    "loli porn",
    "shota porn",
    # Explicit, non-artistic slurs (severe). Do not add common words here —
    # false positives will get the app reported. These are intentionally very obvious.
    " n1gger",
    " kike ",
    " faggot ",
    # Explicit solicitation of violence
    "kill yourself",
    "kys now",
)

_BANNED_PATTERNS = tuple(re.compile(re.escape(s), re.IGNORECASE) for s in _BANNED_SUBSTRINGS)


def contains_banned_keyword(*texts: str | None) -> str | None:
    """Return the matched banned substring, or None if all inputs are clean."""
    for t in texts:
        if not t:
            continue
        for pat, raw in zip(_BANNED_PATTERNS, _BANNED_SUBSTRINGS):
            if pat.search(t):
                return raw.strip()
    return None


async def get_blocked_user_ids(db: AsyncSession, viewer_id: str) -> set[str]:
    """IDs that ``viewer_id`` has blocked, OR who have blocked ``viewer_id``.

    Blocks are symmetric in feed visibility: both directions are hidden so
    neither user sees the other's content.
    """
    result = await db.execute(
        select(UserBlock.blocker_id, UserBlock.blocked_id).where(
            (UserBlock.blocker_id == viewer_id) | (UserBlock.blocked_id == viewer_id)
        )
    )
    blocked: set[str] = set()
    for blocker_id, blocked_id in result.all():
        if blocker_id == viewer_id:
            blocked.add(blocked_id)
        else:
            blocked.add(blocker_id)
    return blocked


async def get_hidden_post_ids(db: AsyncSession, viewer_id: str) -> set[str]:
    """Post IDs that ``viewer_id`` has hidden from their feed."""
    result = await db.execute(select(PostHide.post_id).where(PostHide.user_id == viewer_id))
    return {row[0] for row in result.all()}


def apply_visibility_filter(
    stmt,
    viewer_id: str,
    blocked_user_ids: Iterable[str],
    hidden_post_ids: Iterable[str],
):
    """Add SQLAlchemy WHERE clauses to hide:
      * admin takedowns (is_hidden=True)
      * posts authored by blocked users
      * posts the viewer has hidden
    The viewer's own posts are always visible (even if they show up in a block set).
    """
    stmt = stmt.where(GroundPost.is_hidden.is_(False))
    blocked_ids = {uid for uid in blocked_user_ids if uid and uid != viewer_id}
    if blocked_ids:
        stmt = stmt.where(GroundPost.user_id.notin_(blocked_ids))
    hidden_ids = set(hidden_post_ids)
    if hidden_ids:
        stmt = stmt.where(GroundPost.id.notin_(hidden_ids))
    return stmt


REPORT_REASONS = frozenset(
    {
        "spam",
        "harassment",
        "nsfw",
        "violence",
        "hate",
        "self_harm",
        "illegal",
        "impersonation",
        "other",
    }
)
