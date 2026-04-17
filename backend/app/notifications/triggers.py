"""Event-to-notification triggers.

Call these functions from ground.py, insights.py, mind.py, etc.
They handle rate limiting (1 notification per user per type per minute).

**Background-task safe**: each trigger creates its own DB session so it
works even when called via ``BackgroundTasks`` after the request scope has
closed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import NotificationType, PushNotificationLog
from app.notifications.service import send_notification

logger = logging.getLogger(__name__)

RATE_LIMIT_SECONDS = 60  # 1 per minute per type per user


async def _is_rate_limited(
    db: AsyncSession,
    user_id: str,
    notification_type: str,
) -> bool:
    """Check if a notification was sent to this user for this type recently."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=RATE_LIMIT_SECONDS)
    result = await db.execute(
        select(func.count()).select_from(
            select(PushNotificationLog).where(
                PushNotificationLog.user_id == user_id,
                PushNotificationLog.type == notification_type,
                PushNotificationLog.created_at >= cutoff,
            ).subquery()
        )
    )
    count = result.scalar() or 0
    return count > 0


async def notify_post_liked(
    post_author_id: str,
    liker_name: str,
    post_title: Optional[str] = None,
) -> None:
    """Trigger notification when someone likes a post."""
    async with async_session() as db:
        if await _is_rate_limited(db, post_author_id, NotificationType.POST_LIKED):
            return

        title_snippet = f"「{post_title[:20]}」" if post_title else "你的帖子"
        await send_notification(
            db=db,
            user_id=post_author_id,
            notification_type=NotificationType.POST_LIKED,
            title="收到了一个赞 ❤️",
            body=f"{liker_name} 赞了{title_snippet}",
            data={"type": "post_liked", "author_id": post_author_id},
        )


async def notify_note_liked(
    note_author_id: str,
    liker_name: str,
    note_title: Optional[str] = None,
) -> None:
    """Trigger notification when someone likes a shared note."""
    async with async_session() as db:
        if await _is_rate_limited(db, note_author_id, NotificationType.NOTE_LIKED):
            return

        title_snippet = f"「{note_title[:20]}」" if note_title else "你分享的笔记"
        await send_notification(
            db=db,
            user_id=note_author_id,
            notification_type=NotificationType.NOTE_LIKED,
            title="笔记收到了赞 📝",
            body=f"{liker_name} 赞了{title_snippet}",
            data={"type": "note_liked", "author_id": note_author_id},
        )


async def notify_insight_ready(
    user_id: str,
    insight_id: str,
    insight_title: Optional[str] = None,
) -> None:
    """Trigger notification when an insight report finishes generating."""
    async with async_session() as db:
        if await _is_rate_limited(db, user_id, NotificationType.INSIGHT_READY):
            return

        await send_notification(
            db=db,
            user_id=user_id,
            notification_type=NotificationType.INSIGHT_READY,
            title="洞察已就绪 🔍",
            body=insight_title or "你的洞察分析已完成，点击查看",
            data={"type": "insight_ready", "insight_id": insight_id},
        )


async def notify_mind_connection(
    user_id: str,
    note_a_title: str,
    note_b_title: str,
) -> None:
    """Trigger notification when a new mind graph connection is discovered."""
    async with async_session() as db:
        if await _is_rate_limited(db, user_id, NotificationType.MIND_CONNECTION):
            return

        await send_notification(
            db=db,
            user_id=user_id,
            notification_type=NotificationType.MIND_CONNECTION,
            title="发现新连接 🧠",
            body=f"「{note_a_title[:15]}」↔「{note_b_title[:15]}」",
            data={"type": "mind_connection"},
        )


async def notify_milestone(
    user_id: str,
    milestone: str,
    count: int,
) -> None:
    """Trigger notification for user milestones (e.g., 100 notes)."""
    async with async_session() as db:
        if await _is_rate_limited(db, user_id, NotificationType.MILESTONE):
            return

        await send_notification(
            db=db,
            user_id=user_id,
            notification_type=NotificationType.MILESTONE,
            title="里程碑达成 🎉",
            body=f"你已收集了 {count} 条{milestone}！",
            data={"type": "milestone", "milestone": milestone, "count": count},
        )
