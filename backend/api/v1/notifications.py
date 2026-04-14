"""Push notification API endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.utils import get_current_user
from app.database import get_db
from app.models import User
from app.notifications.service import (
    get_active_tokens,
    get_notification_history,
    get_preferences,
    register_device,
    unregister_device,
    update_preferences,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


# ── Schemas ───────────────────────────────────────────

class RegisterDeviceRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)
    platform: str = Field(..., pattern="^(ios|android)$")
    device_name: Optional[str] = Field(None, max_length=128)


class UnregisterDeviceRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


class PreferenceUpdate(BaseModel):
    enabled: Optional[bool] = None
    post_liked: Optional[bool] = None
    note_liked: Optional[bool] = None
    insight_ready: Optional[bool] = None
    mind_connection: Optional[bool] = None
    milestone: Optional[bool] = None
    quiet_hours_start: Optional[int] = Field(None, ge=0, le=23)
    quiet_hours_end: Optional[int] = Field(None, ge=0, le=23)


# ── Device Token Endpoints ────────────────────────────

@router.post("/devices", status_code=status.HTTP_201_CREATED)
async def register_device_endpoint(
    body: RegisterDeviceRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Register an Expo push token for the current user's device."""
    device = await register_device(
        db=db,
        user_id=user.id,
        token=body.token,
        platform=body.platform,
        device_name=body.device_name,
    )
    return {
        "id": device.id,
        "token": device.token,
        "platform": device.platform,
        "device_name": device.device_name,
        "is_active": device.is_active,
    }


@router.delete("/devices")
async def unregister_device_endpoint(
    body: UnregisterDeviceRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deactivate a device token."""
    success = await unregister_device(db=db, user_id=user.id, token=body.token)
    if not success:
        raise HTTPException(status_code=404, detail="Device token not found")
    return {"status": "ok"}


@router.get("/devices")
async def list_devices(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List active device tokens."""
    tokens = await get_active_tokens(db=db, user_id=user.id)
    return {"tokens": tokens, "count": len(tokens)}


# ── Preferences ───────────────────────────────────────

@router.get("/preferences")
async def get_preferences_endpoint(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get notification preferences."""
    pref = await get_preferences(db=db, user_id=user.id)
    return {
        "enabled": pref.enabled,
        "post_liked": pref.post_liked,
        "note_liked": pref.note_liked,
        "insight_ready": pref.insight_ready,
        "mind_connection": pref.mind_connection,
        "milestone": pref.milestone,
        "quiet_hours_start": pref.quiet_hours_start,
        "quiet_hours_end": pref.quiet_hours_end,
    }


@router.patch("/preferences")
async def update_preferences_endpoint(
    body: PreferenceUpdate = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update notification preferences."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    pref = await update_preferences(db=db, user_id=user.id, updates=updates)
    return {
        "enabled": pref.enabled,
        "post_liked": pref.post_liked,
        "note_liked": pref.note_liked,
        "insight_ready": pref.insight_ready,
        "mind_connection": pref.mind_connection,
        "milestone": pref.milestone,
        "quiet_hours_start": pref.quiet_hours_start,
        "quiet_hours_end": pref.quiet_hours_end,
    }


# ── Notification History ──────────────────────────────

@router.get("/history")
async def get_history(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get paginated notification history."""
    logs, total = await get_notification_history(
        db=db,
        user_id=user.id,
        page=max(1, page),
        page_size=min(50, max(1, page_size)),
    )
    return {
        "items": [
            {
                "id": log.id,
                "type": log.type,
                "title": log.title,
                "body": log.body,
                "status": log.status,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
