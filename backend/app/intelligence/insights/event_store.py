"""Persistent event store for insight generation — replaces process-local buffers.

Every event is written to the ``insight_events`` table so that:
- SSE streaming works across multiple backend workers
- Clients can reconnect and resume from any point (last_sequence)
- Events survive process restarts
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InsightEvent

logger = logging.getLogger(__name__)

# ── In-memory write buffer for batching ──
# Maps generation_id -> list of pending events (not yet flushed to DB)
_write_buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
_flush_locks: dict[str, asyncio.Lock] = {}
_sequence_counters: dict[str, int] = {}

_BATCH_FLUSH_INTERVAL_SECONDS = 0.1
_BATCH_SIZE = 50


async def _get_flush_lock(generation_id: str) -> asyncio.Lock:
    if generation_id not in _flush_locks:
        _flush_locks[generation_id] = asyncio.Lock()
    return _flush_locks[generation_id]


async def _next_sequence(db: AsyncSession, generation_id: str) -> int:
    """Return the next sequence number for a generation.

    Uses an in-memory counter for speed; falls back to DB query on first call
    or after a flush.
    """
    if generation_id in _sequence_counters:
        _sequence_counters[generation_id] += 1
        return _sequence_counters[generation_id]

    result = await db.execute(
        select(InsightEvent.sequence)
        .where(InsightEvent.generation_id == generation_id)
        .order_by(InsightEvent.sequence.desc())
        .limit(1)
    )
    max_seq = result.scalar_one_or_none() or 0
    _sequence_counters[generation_id] = max_seq + 1
    return _sequence_counters[generation_id]


async def append_event(
    db: AsyncSession,
    generation_id: str,
    event: dict[str, Any],
) -> int:
    """Append an event to the persistent store.

    Returns the assigned sequence number. The event may be buffered in memory
    for a short time before hitting the DB — callers that need immediate
    visibility should await ``flush_events()``.
    """
    sequence = await _next_sequence(db, generation_id)

    payload = {
        "generation_id": generation_id,
        "event_type": str(event.get("type", "unknown")),
        "sequence": sequence,
        "group_index": event.get("group") if isinstance(event.get("group"), int) else None,
        "payload_json": json.dumps(event, ensure_ascii=False, default=str),
        "created_at": datetime.now(timezone.utc),
    }

    _write_buffers[generation_id].append(payload)

    # If buffer is large, flush immediately; otherwise let the timer do it.
    if len(_write_buffers[generation_id]) >= _BATCH_SIZE:
        await flush_events(db, generation_id)

    return sequence


async def flush_events(db: AsyncSession, generation_id: str) -> None:
    """Flush all buffered events for a generation to the DB."""
    lock = await _get_flush_lock(generation_id)
    async with lock:
        buffered = _write_buffers.get(generation_id, [])
        if not buffered:
            return

        # Clear the buffer before DB write so concurrent appenders go to new buffer
        _write_buffers[generation_id] = []

        for payload in buffered:
            db.add(InsightEvent(**payload))

        await db.commit()
        logger.debug("Flushed %d events for generation %s", len(buffered), generation_id)


async def get_events(
    db: AsyncSession,
    generation_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 0,
) -> list[InsightEvent]:
    """Read events for a generation, optionally starting after a specific sequence.

    Events are returned ordered by sequence ascending.
    """
    stmt = (
        select(InsightEvent)
        .where(
            InsightEvent.generation_id == generation_id,
            InsightEvent.sequence > after_sequence,
        )
        .order_by(InsightEvent.sequence.asc())
    )
    if limit > 0:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_sequence(db: AsyncSession, generation_id: str) -> int:
    """Return the highest sequence number for a generation (0 if none)."""
    result = await db.execute(
        select(InsightEvent.sequence)
        .where(InsightEvent.generation_id == generation_id)
        .order_by(InsightEvent.sequence.desc())
        .limit(1)
    )
    return result.scalar_one_or_none() or 0


async def get_terminal_event(
    db: AsyncSession,
    generation_id: str,
) -> dict[str, Any] | None:
    """Return the most recent terminal event (completed or error) if any."""
    result = await db.execute(
        select(InsightEvent)
        .where(
            InsightEvent.generation_id == generation_id,
            InsightEvent.event_type.in_(["completed", "error"]),
        )
        .order_by(InsightEvent.sequence.desc())
        .limit(1)
    )
    event = result.scalar_one_or_none()
    if event is None:
        return None
    try:
        return json.loads(event.payload_json)
    except json.JSONDecodeError:
        return None


async def cleanup_old_events(db: AsyncSession, hours: int = 24) -> int:
    """Delete events older than ``hours`` for completed/failed generations.

    Returns the number of rows deleted.
    """
    from datetime import timedelta
    from app.models import InsightGeneration, TaskStatus

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Find old completed/failed generations
    result = await db.execute(
        select(InsightGeneration.id)
        .where(
            InsightGeneration.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]),
            InsightGeneration.completed_at < cutoff,
        )
    )
    old_generation_ids = [row[0] for row in result.all()]

    if not old_generation_ids:
        return 0

    delete_result = await db.execute(
        delete(InsightEvent).where(InsightEvent.generation_id.in_(old_generation_ids))
    )
    await db.commit()
    deleted = delete_result.rowcount or 0
    logger.info("Cleaned up %d old insight events", deleted)
    return deleted


def clear_buffers(generation_id: str) -> None:
    """Clear in-memory buffers for a generation (call after completion)."""
    _write_buffers.pop(generation_id, None)
    _sequence_counters.pop(generation_id, None)
    _flush_locks.pop(generation_id, None)
