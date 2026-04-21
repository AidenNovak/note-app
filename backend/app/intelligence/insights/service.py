"""Streaming broadcast infrastructure, generation lifecycle, and event persistence.

Phase 1 refactor: events are now persisted to the ``insight_events`` table so
that streaming works across multiple workers. The old process-local buffers
are kept as a fallback when the DB table is not yet available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
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

logger = logging.getLogger(__name__)

WORKFLOW_VERSION = "clustered-v1"
STALE_PENDING_TIMEOUT = timedelta(seconds=45)
STALE_PROCESSING_TIMEOUT = timedelta(minutes=20)


# ── DB event store availability flag ──
# Checked once at module load; if the insight_events table doesn't exist we
# fall back to the old in-memory buffers.
_db_events_available: bool | None = None


async def _check_db_events_available() -> bool:
    global _db_events_available
    if _db_events_available is not None:
        return _db_events_available

    try:
        from app.models import InsightEvent
        async with async_session() as db:
            await db.execute(select(InsightEvent).limit(1))
        _db_events_available = True
        logger.info("InsightEvent DB store is available")
    except Exception as exc:
        _db_events_available = False
        logger.warning("InsightEvent DB store not available, falling back to memory buffers: %s", exc)
    return _db_events_available


# ── Legacy in-memory buffers (fallback) ──
_log_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
_event_buffers: dict[str, list[dict[str, object]]] = defaultdict(list)
_terminal_events: dict[str, dict[str, object]] = {}
_delta_snapshots: dict[str, dict[tuple[str, int], str]] = defaultdict(dict)
HIGH_FREQ_EVENT_TYPES = {"token", "thinking_delta", "markdown_delta"}


# ── Generation lifecycle helpers ──


async def get_latest_generation(db: AsyncSession, user_id: str) -> InsightGeneration | None:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(InsightGeneration)
        .where(InsightGeneration.user_id == user_id)
        .order_by(InsightGeneration.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_active_generation(db: AsyncSession, user_id: str) -> InsightGeneration | None:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(InsightGeneration)
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
    from sqlalchemy.orm import selectinload

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
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(InsightReport)
        .options(
            selectinload(InsightReport.evidence_items),
            selectinload(InsightReport.action_items),
            selectinload(InsightReport.generation),
        )
        .where(
            InsightReport.user_id == user_id,
            InsightReport.id == report_id,
        )
    )
    return result.scalar_one_or_none()


# ── Event broadcasting (Phase 1: DB-first with memory fallback) ──


def _sanitize_event_payload(event: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(event, ensure_ascii=False, default=str))


def _update_delta_snapshots(generation_id: str, event: dict[str, object]) -> None:
    event_type = event.get("type")
    group = event.get("group")
    text = event.get("text")
    if isinstance(event_type, str) and event_type in ("thinking_delta", "markdown_delta"):
        if isinstance(group, int) and isinstance(text, str) and text:
            key = (event_type, group)
            _delta_snapshots[generation_id][key] = _delta_snapshots[generation_id].get(key, "") + text


async def broadcast_log(generation_id: str, event: dict[str, object]) -> None:
    """Broadcast an event to all subscribers and persist it.

    Phase 1 behaviour:
    1. Always update the legacy in-memory structures (backward compat).
    2. If the DB event store is available, also persist to ``insight_events``.
    """
    event_type = event.get("type")

    # ── Legacy in-memory updates ──
    _update_delta_snapshots(generation_id, event)
    if event_type in ("completed", "error"):
        _terminal_events[generation_id] = event
        _event_buffers.pop(generation_id, None)
    elif event_type not in HIGH_FREQ_EVENT_TYPES:
        _event_buffers[generation_id].append(event)

    for queue in _log_queues.get(generation_id, []):
        await queue.put(event)

    # ── DB persistence (best-effort) ──
    if await _check_db_events_available():
        try:
            from app.intelligence.insights.event_store import append_event
            async with async_session() as db:
                await append_event(db, generation_id, event)
        except Exception as exc:
            logger.debug("Failed to persist event to DB (using memory fallback): %s", exc)


async def subscribe_to_generation(
    generation_id: str,
    *,
    db: AsyncSession | None = None,
    after_sequence: int = 0,
) -> asyncio.Queue:
    """Subscribe to events for a generation.

    If ``after_sequence`` is provided and the DB event store is available,
    replays events from the DB. Otherwise falls back to the in-memory buffer.
    """
    queue: asyncio.Queue = asyncio.Queue()

    # Try DB replay first
    if after_sequence > 0 and await _check_db_events_available():
        try:
            from app.intelligence.insights.event_store import get_events
            use_db = db
            if use_db is None:
                async with async_session() as inner_db:
                    events = await get_events(inner_db, generation_id, after_sequence=after_sequence)
                    for ev in events:
                        try:
                            payload = json.loads(ev.payload_json)
                        except json.JSONDecodeError:
                            payload = {"type": ev.event_type}
                        queue.put_nowait(payload)
            else:
                events = await get_events(use_db, generation_id, after_sequence=after_sequence)
                for ev in events:
                    try:
                        payload = json.loads(ev.payload_json)
                    except json.JSONDecodeError:
                        payload = {"type": ev.event_type}
                    queue.put_nowait(payload)

            # Also attach to live memory queue for new events
            _log_queues[generation_id].append(queue)
            return queue
        except Exception as exc:
            logger.debug("DB replay failed, falling back to memory: %s", exc)

    # ── Legacy memory replay ──
    for event in _event_buffers.get(generation_id, []):
        queue.put_nowait(event)
    snapshots = _delta_snapshots.get(generation_id, {})
    groups = sorted({group for (_, group) in snapshots.keys()})
    for group in groups:
        thinking = snapshots.get(("thinking_delta", group))
        if thinking:
            queue.put_nowait({"type": "thinking_delta", "group": group, "text": thinking, "snapshot": True})
        markdown = snapshots.get(("markdown_delta", group))
        if markdown:
            queue.put_nowait({"type": "markdown_delta", "group": group, "text": markdown, "snapshot": True})
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


def clear_generation_buffers(generation_id: str) -> None:
    """Clear all in-memory buffers for a generation."""
    _event_buffers.pop(generation_id, None)
    _terminal_events.pop(generation_id, None)
    _delta_snapshots.pop(generation_id, None)
    # Also clear the DB event store buffers
    try:
        from app.intelligence.insights.event_store import clear_buffers as _clear_db_buffers
        _clear_db_buffers(generation_id)
    except Exception:
        pass
