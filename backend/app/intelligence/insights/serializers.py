from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.intelligence.insights.share_cards import build_share_card_model, extract_share_card_payload
from app.models import InsightAgentRun, InsightGeneration, InsightGenerationLog, InsightReport, Note
from app.schemas import (
    InsightActionItemOut,
    InsightAgentRunOut,
    InsightDetailOut,
    InsightEvidenceItemOut,
    InsightGenerationOut,
    InsightGenerationLogOut,
    InsightOut,
    InsightSourceNoteOut,
)


def _aggregate_generation_metrics(runs: list[InsightAgentRun]) -> dict[str, float | int]:
    return {
        "total_duration_ms": sum(run.duration_ms or 0 for run in runs),
        "total_api_duration_ms": sum(run.api_duration_ms or 0 for run in runs),
        "total_cost_usd": round(sum(run.total_cost_usd or 0.0 for run in runs), 6),
        "input_tokens": sum(run.input_tokens or 0 for run in runs),
        "output_tokens": sum(run.output_tokens or 0 for run in runs),
    }


def serialize_generation_log(log: InsightGenerationLog) -> InsightGenerationLogOut:
    try:
        payload = json.loads(log.payload_json or "{}")
    except json.JSONDecodeError:
        payload = None
    return InsightGenerationLogOut(
        id=log.id,
        event_index=log.event_index,
        event_type=log.event_type,
        stage=log.stage,
        group_index=log.group_index,
        message=log.message,
        payload=payload if isinstance(payload, dict) else None,
        created_at=log.created_at,
    )


def serialize_generation(generation: InsightGeneration) -> InsightGenerationOut:
    runs = generation.__dict__.get("agent_runs") or []
    runs = sorted(runs, key=lambda item: item.started_at)
    logs = generation.__dict__.get("logs") or []
    logs = sorted(logs, key=lambda item: (item.event_index, item.created_at))
    totals = _aggregate_generation_metrics(runs)
    return InsightGenerationOut(
        id=generation.id,
        status=generation.status.value,
        workflow_version=generation.workflow_version,
        summary=generation.summary,
        is_active=generation.is_active,
        total_reports=generation.total_reports,
        error=generation.error,
        created_at=generation.created_at,
        updated_at=generation.updated_at,
        completed_at=generation.completed_at,
        total_duration_ms=int(totals["total_duration_ms"]),
        total_api_duration_ms=int(totals["total_api_duration_ms"]),
        total_cost_usd=float(totals["total_cost_usd"]),
        input_tokens=int(totals["input_tokens"]),
        output_tokens=int(totals["output_tokens"]),
        agent_runs=[
            InsightAgentRunOut(
                id=run.id,
                agent_name=run.agent_name,
                stage=run.stage,
                status=run.status,
                session_id=run.session_id,
                model_name=run.model_name,
                duration_ms=run.duration_ms,
                api_duration_ms=run.api_duration_ms,
                total_cost_usd=run.total_cost_usd,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                summary=run.summary,
                error=run.error,
                started_at=run.started_at,
                completed_at=run.completed_at,
            )
            for run in runs
        ],
        logs=[serialize_generation_log(log) for log in logs],
    )


def serialize_report(report: InsightReport) -> InsightOut:
    # Calculate source_notes_count from JSON field
    source_note_ids = json.loads(report.source_note_ids or "[]")
    source_notes_count = len(source_note_ids) if isinstance(source_note_ids, list) else 0
    
    return InsightOut(
        id=report.id,
        generation_id=report.generation_id,
        type=report.type,
        status=report.status,
        title=report.title,
        description=report.description,
        confidence=report.confidence,
        importance_score=report.importance_score,
        novelty_score=report.novelty_score,
        report_version=report.report_version,
        evidence_count=len(report.evidence_items),
        action_items_count=len(report.action_items),
        source_notes_count=source_notes_count,
        created_at=report.created_at,
        generated_at=report.generated_at,
    )


def extract_thinking_trace(report: InsightReport) -> str | None:
    try:
        payload = json.loads(report.report_json or "{}")
    except json.JSONDecodeError:
        return None
    thinking_trace = payload.get("thinking_trace")
    if isinstance(thinking_trace, str) and thinking_trace.strip():
        return thinking_trace
    return None


async def build_report_detail(
    db: AsyncSession,
    user_id: str,
    report: InsightReport,
) -> InsightDetailOut:
    source_note_ids = json.loads(report.source_note_ids or "[]")
    note_ids = set(source_note_ids)
    note_ids.update(item.note_id for item in report.evidence_items)

    notes_by_id: dict[str, Note] = {}
    if note_ids:
        result = await db.execute(
            select(Note)
            .options(selectinload(Note.tags))
            .where(Note.user_id == user_id, Note.id.in_(note_ids))
            .order_by(Note.updated_at.desc())
        )
        notes_by_id = {note.id: note for note in result.scalars().all()}

    source_notes: list[InsightSourceNoteOut] = []
    for note_id in source_note_ids:
        note = notes_by_id.get(note_id)
        if note is None:
            continue
        source_notes.append(
            InsightSourceNoteOut(
                id=note.id,
                title=note.title,
                tags=sorted(t.tag for t in note.tags),
                updated_at=note.updated_at,
            )
        )

    evidence_items = [
        InsightEvidenceItemOut(
            id=item.id,
            note_id=item.note_id,
            note_title=notes_by_id.get(item.note_id).title if item.note_id in notes_by_id else "Unknown note",
            quote=item.quote,
            rationale=item.rationale,
            sort_order=item.sort_order,
        )
        for item in sorted(report.evidence_items, key=lambda evidence: evidence.sort_order)
    ]
    action_items = [
        InsightActionItemOut(
            id=item.id,
            title=item.title,
            detail=item.detail,
            priority=item.priority,
            sort_order=item.sort_order,
        )
        for item in sorted(report.action_items, key=lambda action: action.sort_order)
    ]
    share_card = build_share_card_model(
        report_type=report.type,
        title=report.title,
        description=report.description,
        confidence=report.confidence,
        importance_score=report.importance_score,
        novelty_score=report.novelty_score,
        generated_at=report.generated_at,
        review_summary=report.review_summary,
        evidence_items=evidence_items,
        action_items=action_items,
        raw_share_card=extract_share_card_payload(report.report_json),
    )

    return InsightDetailOut(
        **serialize_report(report).model_dump(),
        report_markdown=report.report_markdown,
        thinking_trace=extract_thinking_trace(report),
        review_summary=report.review_summary,
        source_notes=source_notes,
        evidence_items=evidence_items,
        action_items=action_items,
        share_card=share_card,
        generation=serialize_generation(report.generation) if report.generation is not None else None,
    )
