"""Simplified Insight Pipeline — parallel multi-theme report generation.

Removes graph clustering and angle discovery; instead feeds the user's notes
directly to 3 parallel LLM calls with different thematic lenses. Each call
streams its thinking and markdown live to the SSE pipe so the client sees
3 reports being written concurrently.

Event flow (per generation):
  starting → progress → group_started ×3 → (thinking_delta + markdown_delta) ×3
  → group_completed ×3 → completed
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.intelligence.insights.llm import (
    discover_angles_from_notes,
    extract_report_metadata,
    write_report_markdown,
)
from app.intelligence.insights.schemas_ai import InsightReportOutput
from app.intelligence.insights.service import broadcast_log, clear_generation_buffers
from app.models import (
    InsightActionItem,
    InsightEvidenceItem,
    InsightGeneration,
    InsightReport,
    Note,
    NoteTag,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# Max notes to include per generation (keeps cost / latency reasonable)
MAX_NOTES = 30
# Max chars of note content per report (prevent token overflow)
MAX_CONTENT_CHARS = 18000

# Fallback themes when angle discovery fails
_FALLBACK_THEMES: list[tuple[str, str, str]] = [
    ("pattern", "模式识别", "发现笔记中的重复主题、行为模式和内在结构"),
    ("connection", "关联分析", "发现笔记之间的隐藏联系、跨领域关联和知识网络"),
    ("trend", "趋势洞察", "发现时间维度的变化趋势、发展方向和演进脉络"),
]


async def _fetch_notes(db: AsyncSession, user_id: str) -> list[dict]:
    """Fetch user's notes with tags, ordered by recency."""
    result = await db.execute(
        select(Note)
        .options(selectinload(Note.tags))
        .where(Note.user_id == user_id)
        .order_by(Note.updated_at.desc())
    )
    notes_raw = result.scalars().all()

    notes: list[dict] = []
    for n in notes_raw:
        content = n.markdown_content or ""
        notes.append({
            "id": n.id,
            "title": n.title or "Untitled",
            "content": content,
            "tags": [t.tag for t in n.tags],
            "created_at": n.created_at.isoformat() if n.created_at else "",
            "updated_at": n.updated_at.isoformat() if n.updated_at else "",
        })
    return notes


def _sample_notes(notes: list[dict], max_notes: int = MAX_NOTES) -> list[dict]:
    """Deterministically sample notes when there are too many."""
    if len(notes) <= max_notes:
        return notes

    # Prioritize notes with more tags and longer content
    scored = sorted(
        notes,
        key=lambda n: (len(n.get("tags", [])), len(n.get("content", ""))),
        reverse=True,
    )
    deterministic_count = int(max_notes * 0.6)
    pool = scored[deterministic_count:]
    import random
    random_pick = random.sample(pool, min(len(pool), max_notes - deterministic_count))
    return scored[:deterministic_count] + random_pick


def _build_notes_content(notes: list[dict], max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Build a single notes content block for LLM prompt."""
    parts: list[str] = []
    total_chars = 0
    for note in notes:
        tags = ", ".join(note.get("tags", [])) or "无标签"
        block = (
            f"### {note['title']} (ID: {note['id']})\n"
            f"标签: {tags} | 更新于: {note.get('updated_at', '未知')}\n\n"
            f"{note['content']}\n"
        )
        if total_chars + len(block) > max_chars:
            remaining = max_chars - total_chars
            if remaining < 300:
                break
            block = block[:remaining] + "\n...(截断)"
        parts.append(block)
        total_chars += len(block)
    return "\n---\n".join(parts)


async def _generate_single_report(
    *,
    generation_id: str,
    theme_hint: str,
    theme_name: str,
    theme_desc: str,
    group_index: int,
    total_groups: int,
    notes_content: str,
    note_index: list[tuple[str, str]],
    note_count: int,
    date: str,
) -> InsightReportOutput | None:
    """Generate one insight report with live SSE streaming."""
    await broadcast_log(generation_id, {
        "type": "group_started",
        "group": group_index,
        "total_groups": total_groups,
        "theme": theme_name,
        "angle": theme_desc,
        "note_count": note_count,
    })

    try:
        # Step 1: stream markdown
        write_result = await write_report_markdown(
            angle_name=theme_name,
            angle_description=theme_desc,
            type_hint=theme_hint,
            notes_content=notes_content,
            generation_id=generation_id,
            group_index=group_index,
        )

        # Step 2: extract structured metadata
        extraction = await extract_report_metadata(
            markdown=write_result.text,
            angle_name=theme_name,
            type_hint=theme_hint,
            note_index=note_index,
            note_count=note_count,
            date=date,
        )

        report = InsightReportOutput(
            title=extraction.title,
            description=extraction.description,
            type=extraction.type,
            report_markdown=write_result.text,
            thinking_trace=write_result.reasoning or None,
            confidence=extraction.confidence,
            importance_score=extraction.importance_score,
            novelty_score=extraction.novelty_score,
            evidence_items=extraction.evidence_items,
            action_items=extraction.action_items,
            share_card=extraction.share_card,
        )

        await broadcast_log(generation_id, {
            "type": "group_completed",
            "group": group_index,
            "total_groups": total_groups,
            "theme": theme_name,
            "title": report.title,
            "description": report.description,
            "thinking_trace": report.thinking_trace or "",
            "report_markdown": report.report_markdown,
        })

        return report

    except Exception as exc:
        logger.warning("Report generation failed for theme '%s': %s", theme_name, exc)
        await broadcast_log(generation_id, {
            "type": "group_completed",
            "group": group_index,
            "total_groups": total_groups,
            "theme": theme_name,
            "title": "",
            "description": f"生成失败: {exc}",
        })
        return None


async def _persist_reports(
    db: AsyncSession,
    generation: InsightGeneration,
    reports: list[InsightReportOutput],
    all_notes: list[dict],
) -> None:
    """Persist generated reports to the database."""
    from app.intelligence.insights.share_cards import build_share_card_payload

    generation_id = generation.id
    user_id = generation.user_id
    generated_at = datetime.now(timezone.utc)
    valid_note_ids = {n["id"] for n in all_notes}

    # Deactivate old generations
    await db.execute(
        update(InsightGeneration)
        .where(InsightGeneration.user_id == user_id, InsightGeneration.id != generation.id)
        .values(is_active=False)
    )

    for idx, report_obj in enumerate(reports, 1):
        report_id = str(uuid.uuid4())
        evidence_items = [ev.model_dump() for ev in report_obj.evidence_items]
        action_items = [act.model_dump() for act in report_obj.action_items]

        # Build share card
        build_share_card_payload(
            report_type=report_obj.type,
            title=report_obj.title,
            description=report_obj.description,
            confidence=report_obj.confidence,
            importance_score=report_obj.importance_score,
            novelty_score=report_obj.novelty_score,
            generated_at=generated_at,
            evidence_items=evidence_items,
            action_items=action_items,
            raw_share_card=report_obj.share_card.model_dump() if report_obj.share_card else None,
        )

        # Validate evidence note_ids
        validated_evidence = []
        for ev in evidence_items:
            nid = ev.get("note_id", "")
            if nid not in valid_note_ids:
                # fallback to first available note
                nid = next(iter(valid_note_ids)) if valid_note_ids else ""
            validated_evidence.append({**ev, "note_id": nid})

        report_dict = report_obj.model_dump()
        db.add(InsightReport(
            id=report_id,
            generation_id=generation_id,
            user_id=user_id,
            type=report_obj.type,
            status="published",
            title=report_obj.title,
            description=report_obj.description,
            report_version=1,
            confidence=report_obj.confidence,
            importance_score=report_obj.importance_score,
            novelty_score=report_obj.novelty_score,
            review_summary=None,
            card_rank=idx,
            report_markdown=report_obj.report_markdown,
            report_json=json.dumps(report_dict, ensure_ascii=False),
            source_note_ids=json.dumps(list(valid_note_ids)),
            generated_at=generated_at,
        ))

        for ev_idx, ev in enumerate(validated_evidence, 1):
            db.add(InsightEvidenceItem(
                id=str(uuid.uuid4()),
                report_id=report_id,
                note_id=ev["note_id"],
                quote=str(ev.get("quote", ""))[:500],
                rationale=str(ev.get("rationale", ""))[:500],
                sort_order=ev_idx,
            ))

        for act_idx, act in enumerate(action_items, 1):
            db.add(InsightActionItem(
                id=str(uuid.uuid4()),
                report_id=report_id,
                title=str(act.get("title", ""))[:255],
                detail=str(act.get("detail", ""))[:500],
                priority=str(act.get("priority", "medium"))[:16],
                sort_order=act_idx,
            ))

    generation.status = TaskStatus.COMPLETED
    generation.total_reports = len(reports)
    generation.completed_at = generated_at
    generation.is_active = True
    generation.workflow_version = "parallel-v1"
    generation.summary = f"生成了 {len(reports)} 篇洞察报告，分析了 {len(all_notes)} 条笔记"
    generation.error = None

    await db.commit()

    await broadcast_log(generation_id, {
        "type": "completed",
        "summary": generation.summary,
    })


async def run_pipeline(db: AsyncSession, generation: InsightGeneration) -> None:
    """Main pipeline entry point.

    Phase 0: Dynamic angle discovery (1 fast LLM call)
    Phase 1: Parallel report generation (N streaming LLM calls)
    """
    generation_id = generation.id
    user_id = generation.user_id
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    await broadcast_log(generation_id, {
        "type": "starting",
        "message": "Insight Agent 启动...",
    })

    # ── Fetch notes ──
    notes = await _fetch_notes(db, user_id)
    await db.rollback()  # release read-only tx

    if not notes:
        await broadcast_log(generation_id, {
            "type": "error",
            "message": "请先添加一些笔记再生成洞察。",
        })
        generation.status = TaskStatus.FAILED
        generation.error = "请先添加一些笔记再生成洞察。"
        generation.completed_at = datetime.now(timezone.utc)
        generation.is_active = False
        await db.commit()
        return

    sampled = _sample_notes(notes)
    note_count = len(sampled)
    notes_content = _build_notes_content(sampled)
    note_index = [(n["id"], n["title"]) for n in sampled]

    # ── Phase 0: Dynamic angle discovery ──
    await broadcast_log(generation_id, {
        "type": "progress",
        "message": f"正在分析 {note_count} 条笔记，发现洞察角度...",
    })

    themes: list[tuple[str, str, str]] = []
    try:
        angle_result = await discover_angles_from_notes(
            notes_content=notes_content,
            note_count=note_count,
        )
        for angle in angle_result.angles:
            themes.append((
                angle.type_hint or "pattern",
                angle.angle_name,
                angle.description,
            ))
        await broadcast_log(generation_id, {
            "type": "progress",
            "message": f"发现 {len(themes)} 个分析角度：{', '.join(t[1] for t in themes)}",
        })
    except Exception as exc:
        logger.warning("Angle discovery failed, using fallback themes: %s", exc)
        themes = list(_FALLBACK_THEMES)
        await broadcast_log(generation_id, {
            "type": "progress",
            "message": f"使用默认分析角度：{', '.join(t[1] for t in themes)}",
        })

    total_groups = len(themes)

    # ── Phase 1: Parallel generation ──
    async def generate_with_theme(
        theme_hint: str, theme_name: str, theme_desc: str, group_index: int
    ) -> InsightReportOutput | None:
        return await _generate_single_report(
            generation_id=generation_id,
            theme_hint=theme_hint,
            theme_name=theme_name,
            theme_desc=theme_desc,
            group_index=group_index,
            total_groups=total_groups,
            notes_content=notes_content,
            note_index=note_index,
            note_count=note_count,
            date=today,
        )

    tasks = [
        generate_with_theme(hint, name, desc, i + 1)
        for i, (hint, name, desc) in enumerate(themes)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out failures
    reports: list[InsightReportOutput] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Parallel report task failed: %s", r)
            continue
        if r is not None:
            reports.append(r)

    if not reports:
        await broadcast_log(generation_id, {
            "type": "error",
            "message": "所有报告生成均失败。",
        })
        generation.status = TaskStatus.FAILED
        generation.error = "所有报告生成均失败。"
        generation.completed_at = datetime.now(timezone.utc)
        generation.is_active = False
        await db.commit()
        clear_generation_buffers(generation_id)
        return

    # ── Persist ──
    async with async_session() as persist_db:
        gen = await persist_db.get(InsightGeneration, generation_id)
        if gen is not None:
            await _persist_reports(persist_db, gen, reports, sampled)

    logger.info(
        "Pipeline completed: %d reports, generation=%s",
        len(reports), generation_id,
    )
