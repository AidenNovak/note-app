from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.intelligence.agent_engine import agent_engine
from app.intelligence.insights.context import fetch_note_context, write_workspace
from app.intelligence.insights.normalizers import normalize_agent_runs, normalize_reports
from app.intelligence.insights.profiles import INSIGHT_WORKFLOW_VERSION, build_insight_task_config
from app.intelligence.insights.serializers import (
    build_report_detail,
    serialize_generation,
    serialize_report,
)
from app.models import (
    InsightActionItem,
    InsightAgentRun,
    InsightEvidenceItem,
    InsightGeneration,
    InsightReport,
    TaskStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


async def create_generation(db: AsyncSession, user_id: str) -> tuple[InsightGeneration, bool]:
    existing = await get_active_generation(db, user_id)
    if existing is not None:
        return existing, False

    generation = InsightGeneration(
        id=str(uuid.uuid4()),
        user_id=user_id,
        status=TaskStatus.PENDING,
        workflow_version=INSIGHT_WORKFLOW_VERSION,
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


def subscribe_to_generation(generation_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    # Replay buffered events so late subscribers don't miss anything
    for event in _event_buffers.get(generation_id, []):
        queue.put_nowait(event)
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
    # Buffer non-token events for late subscribers (tokens are too many to buffer)
    if event.get("type") != "token":
        _event_buffers[generation_id].append(event)
    # Clean up buffer on terminal events
    if event.get("type") in ("completed", "error"):
        _event_buffers.pop(generation_id, None)
    for queue in _log_queues.get(generation_id, []):
        await queue.put(event)


async def process_generation(generation_id: str, db: AsyncSession | None = None) -> None:
    if db is None:
        async with async_session() as session:
            await _process_generation(session, generation_id)
        return
    await _process_generation(db, generation_id)


async def _process_generation(db: AsyncSession, generation_id: str) -> None:
    generation = await db.get(InsightGeneration, generation_id)
    if generation is None:
        return

    generation.status = TaskStatus.PROCESSING
    generation.error = None
    generation.summary = "Cloud SDK is reviewing recent notes and coordinating specialist stages."
    generation.workflow_version = INSIGHT_WORKFLOW_VERSION
    await db.commit()

    try:
        await broadcast_log(generation_id, {"type": "starting", "message": "Preparing context..."})
        notes = await fetch_note_context(db, generation.user_id)
        if not notes:
            raise RuntimeError("Add a few notes before generating insights.")

        workspace_path = write_workspace(generation.id, notes)
        generation.workspace_path = str(workspace_path)
        await db.commit()

        payload = None
        task_config = build_insight_task_config()
        async for event in agent_engine.run_task(
            task_name=f"insight_{generation.id}",
            task_config=task_config,
            workspace_path=workspace_path,
        ):
            if event["type"] == "progress":
                await broadcast_log(generation_id, event["data"])
            elif event["type"] == "final":
                payload = event["data"]

        if not payload:
            raise RuntimeError("Cloud SDK insight workflow did not return a final payload.")

        reports = normalize_reports(payload.get("reports", []), {note["id"] for note in notes})
        agent_runs = normalize_agent_runs(payload.get("agent_runs", []))
        if not reports:
            raise RuntimeError("Cloud SDK did not return any insight reports.")

        generated_at = _utcnow()
        await db.execute(
            update(InsightGeneration)
            .where(
                InsightGeneration.user_id == generation.user_id,
                InsightGeneration.id != generation.id,
            )
            .values(is_active=False)
        )
        await db.execute(delete(InsightAgentRun).where(InsightAgentRun.generation_id == generation.id))
        await db.execute(delete(InsightReport).where(InsightReport.generation_id == generation.id))

        generation.workflow_version = str(
            payload.get("workflow_version") or task_config.get("workflow_version") or INSIGHT_WORKFLOW_VERSION
        )[:64]
        generation.session_id = str(payload.get("session_id"))[:128] if payload.get("session_id") else None
        generation.summary = str(payload.get("summary") or f"Generated {len(reports)} insight reports.").strip()
        generation.status = TaskStatus.COMPLETED
        generation.total_reports = len(reports)
        generation.completed_at = generated_at
        generation.error = None
        generation.is_active = True

        for run_payload in agent_runs:
            db.add(
                InsightAgentRun(
                    id=str(uuid.uuid4()),
                    generation_id=generation.id,
                    agent_name=run_payload["agent_name"],
                    stage=run_payload["stage"],
                    status=run_payload["status"],
                    session_id=run_payload["session_id"],
                    model_name=run_payload["model_name"],
                    duration_ms=run_payload["duration_ms"],
                    api_duration_ms=run_payload["api_duration_ms"],
                    total_cost_usd=run_payload["total_cost_usd"],
                    input_tokens=run_payload["input_tokens"],
                    output_tokens=run_payload["output_tokens"],
                    summary=run_payload["summary"],
                    output_json=json.dumps(run_payload.get("output"), ensure_ascii=False)
                    if run_payload.get("output") is not None
                    else None,
                    error=run_payload["error"],
                    started_at=run_payload["started_at"],
                    completed_at=run_payload["completed_at"],
                )
            )

        for index, payload_report in enumerate(reports, start=1):
            report_id = str(uuid.uuid4())
            db.add(
                InsightReport(
                    id=report_id,
                    generation_id=generation.id,
                    user_id=generation.user_id,
                    type=payload_report["type"],
                    status=payload_report["status"],
                    title=payload_report["title"],
                    description=payload_report["description"],
                    report_version=1,
                    confidence=payload_report["confidence"],
                    importance_score=payload_report["importance_score"],
                    novelty_score=payload_report["novelty_score"],
                    review_summary=payload_report["review_summary"],
                    card_rank=index,
                    report_markdown=payload_report["report_markdown"],
                    report_json=json.dumps(payload_report, ensure_ascii=False),
                    source_note_ids=json.dumps(payload_report["source_note_ids"]),
                    generated_at=generated_at,
                )
            )
            for evidence_index, evidence in enumerate(payload_report["evidence_items"], start=1):
                db.add(
                    InsightEvidenceItem(
                        id=str(uuid.uuid4()),
                        report_id=report_id,
                        note_id=evidence["note_id"],
                        quote=evidence["quote"],
                        rationale=evidence["rationale"],
                        sort_order=evidence_index,
                    )
                )
            for action_index, action in enumerate(payload_report["action_items"], start=1):
                db.add(
                    InsightActionItem(
                        id=str(uuid.uuid4()),
                        report_id=report_id,
                        title=action["title"],
                        detail=action["detail"],
                        priority=action["priority"],
                        sort_order=action_index,
                    )
                )

        await db.commit()
        await broadcast_log(generation_id, {"type": "completed", "summary": generation.summary})

    except Exception as exc:
        await db.rollback()
        generation.status = TaskStatus.FAILED
        generation.error = str(exc)
        generation.completed_at = _utcnow()
        generation.is_active = False
        await db.commit()
        await broadcast_log(generation_id, {"type": "error", "message": str(exc)})
