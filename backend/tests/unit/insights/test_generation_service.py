from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intelligence.insights.service import (
    build_terminal_event,
    broadcast_log,
    create_generation,
    subscribe_to_generation,
    unsubscribe_from_generation,
)
from app.models import InsightGeneration, TaskStatus


pytestmark = pytest.mark.asyncio


async def _create_generation(
    db: AsyncSession,
    user_id: str,
    *,
    status: TaskStatus,
    created_at: datetime,
    updated_at: datetime | None = None,
) -> InsightGeneration:
    generation = InsightGeneration(
        id=str(uuid.uuid4()),
        user_id=user_id,
        status=status,
        created_at=created_at,
        updated_at=updated_at or created_at,
        is_active=False,
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)
    return generation


class TestCreateGeneration:
    async def test_reuses_fresh_pending_generation(self, db: AsyncSession, test_user):
        fresh = await _create_generation(
            db,
            test_user.id,
            status=TaskStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )

        generation, created = await create_generation(db, test_user.id)

        assert created is False
        assert generation.id == fresh.id

    async def test_replaces_stale_pending_generation(self, db: AsyncSession, test_user):
        stale = await _create_generation(
            db,
            test_user.id,
            status=TaskStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )

        generation, created = await create_generation(db, test_user.id)

        assert created is True
        assert generation.id != stale.id

        refreshed = await db.execute(select(InsightGeneration).where(InsightGeneration.id == stale.id))
        stale_generation = refreshed.scalar_one()
        assert stale_generation.status == TaskStatus.FAILED
        assert stale_generation.error == "Previous insight generation was interrupted before completion."


class TestGenerationStreaming:
    async def test_build_terminal_event_for_failed_generation(self, db: AsyncSession, test_user):
        generation = await _create_generation(
            db,
            test_user.id,
            status=TaskStatus.FAILED,
            created_at=datetime.now(timezone.utc),
        )
        generation.error = "OpenRouter credits exhausted"

        event = build_terminal_event(generation)

        assert event == {
            "type": "error",
            "message": "OpenRouter credits exhausted",
        }

    async def test_subscribe_replays_terminal_event_for_late_listener(self):
        generation_id = str(uuid.uuid4())
        queue = None

        try:
            await broadcast_log(generation_id, {"type": "error", "message": "boom"})
            queue = subscribe_to_generation(generation_id)

            assert queue.get_nowait() == {"type": "error", "message": "boom"}
        finally:
            if queue is not None:
                unsubscribe_from_generation(generation_id, queue)
