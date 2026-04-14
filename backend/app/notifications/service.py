"""Push notification service — orchestrates token management, preferences, and delivery."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DeviceToken,
    NotificationPreference,
    NotificationType,
    PushNotificationLog,
)
from app.notifications.expo_client import send_push

logger = logging.getLogger(__name__)


# ── Device Token Management ───────────────────────────

async def register_device(
    db: AsyncSession,
    user_id: str,
    token: str,
    platform: str,
    device_name: Optional[str] = None,
) -> DeviceToken:
    """Register or reactivate an Expo push token for a user."""
    result = await db.execute(
        select(DeviceToken).where(
            DeviceToken.user_id == user_id,
            DeviceToken.token == token,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.is_active = True
        existing.platform = platform
        existing.device_name = device_name or existing.device_name
        existing.last_used_at = datetime.now(timezone.utc)
        await db.commit()
        return existing

    device = DeviceToken(
        id=str(uuid.uuid4()),
        user_id=user_id,
        token=token,
        platform=platform,
        device_name=device_name,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


async def unregister_device(db: AsyncSession, user_id: str, token: str) -> bool:
    """Deactivate a device token."""
    result = await db.execute(
        select(DeviceToken).where(
            DeviceToken.user_id == user_id,
            DeviceToken.token == token,
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        return False

    device.is_active = False
    await db.commit()
    return True


async def get_active_tokens(db: AsyncSession, user_id: str) -> list[str]:
    """Get all active Expo push tokens for a user."""
    result = await db.execute(
        select(DeviceToken.token).where(
            DeviceToken.user_id == user_id,
            DeviceToken.is_active == True,
        )
    )
    return [row[0] for row in result.all()]


# ── Notification Preferences ─────────────────────────

async def get_preferences(db: AsyncSession, user_id: str) -> NotificationPreference:
    """Get or create notification preferences for a user."""
    result = await db.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    )
    pref = result.scalar_one_or_none()
    if pref:
        return pref

    pref = NotificationPreference(
        id=str(uuid.uuid4()),
        user_id=user_id,
    )
    db.add(pref)
    await db.commit()
    await db.refresh(pref)
    return pref


async def update_preferences(
    db: AsyncSession,
    user_id: str,
    updates: dict,
) -> NotificationPreference:
    """Update notification preferences."""
    pref = await get_preferences(db, user_id)

    allowed_fields = {
        "enabled", "post_liked", "note_liked", "insight_ready",
        "mind_connection", "milestone", "quiet_hours_start", "quiet_hours_end",
    }
    for key, value in updates.items():
        if key in allowed_fields:
            setattr(pref, key, value)

    await db.commit()
    await db.refresh(pref)
    return pref


def is_type_enabled(pref: NotificationPreference, notification_type: str) -> bool:
    """Check if a notification type is enabled in user preferences."""
    if not pref.enabled:
        return False
    type_field_map = {
        NotificationType.POST_LIKED: "post_liked",
        NotificationType.NOTE_LIKED: "note_liked",
        NotificationType.INSIGHT_READY: "insight_ready",
        NotificationType.MIND_CONNECTION: "mind_connection",
        NotificationType.MILESTONE: "milestone",
        NotificationType.SYSTEM: None,  # system always delivered
    }
    field = type_field_map.get(notification_type)
    if field is None:
        return True  # system/unknown types always pass
    return getattr(pref, field, True)


# ── Send Notifications ────────────────────────────────

async def send_notification(
    db: AsyncSession,
    user_id: str,
    notification_type: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> Optional[PushNotificationLog]:
    """Send a push notification to a user, respecting their preferences.

    Returns the log entry if sent, None if skipped.
    """
    # Check preferences
    pref = await get_preferences(db, user_id)
    if not is_type_enabled(pref, notification_type):
        logger.debug("notification_skipped type=%s user=%s (disabled)", notification_type, user_id)
        return None

    # Get tokens
    tokens = await get_active_tokens(db, user_id)
    if not tokens:
        logger.debug("notification_skipped type=%s user=%s (no tokens)", notification_type, user_id)
        return None

    # Send via Expo
    tickets = await send_push(tokens, title, body, data=data)

    # Log
    status = "sent"
    error = None
    if not tickets:
        status = "failed"
        error = "No tickets returned"
    elif any(t.get("status") == "error" for t in tickets):
        failed = [t for t in tickets if t.get("status") == "error"]
        if len(failed) == len(tickets):
            status = "failed"
        error = failed[0].get("message", "Unknown error")

    log = PushNotificationLog(
        id=str(uuid.uuid4()),
        user_id=user_id,
        type=notification_type,
        title=title,
        body=body,
        data_json=json.dumps(data) if data else None,
        status=status,
        error=error,
    )
    db.add(log)
    await db.commit()

    return log


async def get_notification_history(
    db: AsyncSession,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PushNotificationLog], int]:
    """Get paginated notification history for a user."""
    from sqlalchemy import func

    count_q = select(func.count()).select_from(
        select(PushNotificationLog).where(PushNotificationLog.user_id == user_id).subquery()
    )
    total = (await db.execute(count_q)).scalar() or 0

    result = await db.execute(
        select(PushNotificationLog)
        .where(PushNotificationLog.user_id == user_id)
        .order_by(PushNotificationLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()

    return logs, total
