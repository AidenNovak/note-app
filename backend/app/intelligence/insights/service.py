from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.intelligence.insights.serializers import (
    build_report_detail,
    serialize_generation,
    serialize_report,
)
from app.models import (
    InsightGeneration,
    InsightReport,
    TaskStatus,
)

WORKFLOW_VERSION = "clustered-v1"
STALE_PENDING_TIMEOUT = timedelta(seconds=45)
STALE_PROCESSING_TIMEOUT = timedelta(minutes=20)


async def get_latest_generation(db: AsyncSession, user_id: str) -> InsightGeneration | None:
    result = await db.execute(
        select(InsightGeneration)
        .options(selectinload(InsightGeneration.agent_runs))
        .where(InsightGeneration.user_id == user_id)
        .order_by(InsightGeneration.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_active_generation(db: AsyncSession, user_id: str) -> InsightGeneration | None:
    result = await db.execute(
        select(InsightGeneration)
        .options(selectinload(InsightGeneration.agent_runs))
        .where(
            InsightGeneration.user_id == user_id,
            InsightGeneration.status.in_([TaskStatus.PENDING, TaskStatus.PROCESSING]),
        )
        .order_by(InsightGeneration.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_stale_generation(generation: InsightGeneration, now: datetime) -> bool:
    age = _as_utc(now) - _as_utc(generation.updated_at or generation.created_at)
    if generation.status == TaskStatus.PENDING:
        return age > STALE_PENDING_TIMEOUT
    if generation.status == TaskStatus.PROCESSING:
        return age > STALE_PROCESSING_TIMEOUT
    return False


async def create_generation(db: AsyncSession, user_id: str) -> tuple[InsightGeneration, bool]:
    existing = await get_active_generation(db, user_id)
    if existing is not None:
        now = datetime.now(timezone.utc)
        if not _is_stale_generation(existing, now):
            return existing, False

        existing.status = TaskStatus.FAILED
        existing.error = "Previous insight generation was interrupted before completion."
        existing.is_active = False
        existing.completed_at = now
        await db.commit()

    generation = InsightGeneration(
        id=str(uuid.uuid4()),
        user_id=user_id,
        status=TaskStatus.PENDING,
        workflow_version=WORKFLOW_VERSION,
        is_active=False,
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)
    return generation, True


async def list_reports(db: AsyncSession, user_id: str) -> list[InsightReport]:
    result = await db.execute(
        select(InsightReport)
        .options(
            selectinload(InsightReport.evidence_items),
            selectinload(InsightReport.action_items),
        )
        .join(InsightGeneration, InsightReport.generation_id == InsightGeneration.id)
        .where(
            InsightReport.user_id == user_id,
            InsightGeneration.status == TaskStatus.COMPLETED,
        )
        .order_by(InsightReport.generated_at.desc())
    )
    return list(result.scalars().all())


async def get_report(db: AsyncSession, user_id: str, report_id: str) -> InsightReport | None:
    result = await db.execute(
        select(InsightReport)
        .options(
            selectinload(InsightReport.evidence_items),
            selectinload(InsightReport.action_items),
            selectinload(InsightReport.generation).selectinload(InsightGeneration.agent_runs),
        )
        .where(
            InsightReport.user_id == user_id,
            InsightReport.id == report_id,
        )
    )
    return result.scalar_one_or_none()


_log_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
_event_buffers: dict[str, list[dict]] = defaultdict(list)
_terminal_events: dict[str, dict[str, object]] = {}


def build_terminal_event(generation: InsightGeneration) -> dict[str, object] | None:
    if generation.status == TaskStatus.COMPLETED:
        return {
            "type": "completed",
            "summary": generation.summary,
        }
    if generation.status == TaskStatus.FAILED:
        return {
            "type": "error",
            "message": (generation.error or "Generation failed")[:300],
        }
    return None


def subscribe_to_generation(generation_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    # Replay buffered events so late subscribers don't miss anything
    for event in _event_buffers.get(generation_id, []):
        queue.put_nowait(event)
    terminal_event = _terminal_events.get(generation_id)
    if terminal_event is not None:
        queue.put_nowait(terminal_event)
    _log_queues[generation_id].append(queue)
    return queue


def unsubscribe_from_generation(generation_id: str, queue: asyncio.Queue) -> None:
    if generation_id not in _log_queues:
        return
    if queue in _log_queues[generation_id]:
        _log_queues[generation_id].remove(queue)
    if not _log_queues[generation_id]:
        del _log_queues[generation_id]


async def broadcast_log(generation_id: str, event: dict[str, object]) -> None:
    # Buffer milestone events for late subscribers; skip high-frequency
    # streaming deltas which would balloon the buffer.
    event_type = event.get("type")
    HIGH_FREQ = {"token", "thinking_delta", "markdown_delta"}
    if event_type in ("completed", "error"):
        _terminal_events[generation_id] = event
        _event_buffers.pop(generation_id, None)
    elif event_type not in HIGH_FREQ:
        _event_buffers[generation_id].append(event)
    for queue in _log_queues.get(generation_id, []):
        await queue.put(event)
