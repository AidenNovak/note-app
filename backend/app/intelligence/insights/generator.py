"""Direct insight generation using AI SDK structured output.

Replaces the manual OpenRouter HTTP + JSON parsing approach with
ai-sdk-python generate_object for reliable structured output.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import (
    InsightActionItem,
    InsightEvidenceItem,
    InsightGeneration,
    InsightReport,
    MindConnection,
    Note,
    NoteTag,
    NoteSimilarity,
    TaskStatus,
)
from app.intelligence.insights.llm import get_model, generate_report
from app.intelligence.insights.schemas_ai import InsightReportOutput

logger = logging.getLogger(__name__)

REPORT_SYSTEM_PROMPT = """\
You are an expert knowledge analyst. Given a user's notes and the discovered \
connections between them, produce a comprehensive insight report.

Output STRICT JSON with this schema:
{
  "title": "Report title (compelling, conclusion-driven)",
  "description": "2-3 sentence executive summary",
  "type": "report",
  "report_markdown": "Full markdown report, 1500-2000 words. MUST include:\
 ## Executive Summary, ## Key Findings (3-5 findings with data), \
## Connection Map (describe how notes relate, use ASCII table or list), \
## Trend Analysis, ## Recommendations. Use **bold** for emphasis, \
> blockquotes for key insights, and numbered lists for actionable items.",
  "confidence": 0.0-1.0,
  "importance_score": 0.0-1.0,
  "novelty_score": 0.0-1.0,
  "evidence_items": [
    {"note_id": "...", "quote": "exact quote from note", "rationale": "why this matters"}
  ],
  "action_items": [
    {"title": "Action title", "detail": "Specific steps", "priority": "high|medium|low"}
  ],
  "share_card": {
    "theme": "report",
    "eyebrow": "INSIGHT REPORT",
    "headline": "Compelling headline ≤80 chars",
    "summary": "Key takeaway in 2-3 sentences",
    "highlight": "Most surprising finding",
    "evidence_quote": "Best supporting quote",
    "evidence_source": "Source note title",
    "action_title": "Top recommended action",
    "action_detail": "Brief action detail",
    "metrics": [{"label": "Notes Analyzed", "value": "N"}, {"label": "Connections", "value": "N"}],
    "footer": "Generated on YYYY-MM-DD"
  }
}

Write the report in the SAME LANGUAGE as the user's notes. If notes are in Chinese, \
write the entire report in Chinese. If mixed, prefer Chinese.

The report_markdown MUST be 1500-2000 words. Be thorough and analytical.
Include specific quotes and references to actual note content.
"""


async def _fetch_context(db: AsyncSession, user_id: str) -> dict:
    """Gather notes, tags, connections, and similarities for the prompt."""
    # Recent notes (up to 15)
    result = await db.execute(
        select(Note)
        .options(selectinload(Note.tags))
        .where(Note.user_id == user_id)
        .order_by(Note.updated_at.desc())
        .limit(settings.INSIGHT_MAX_CONTEXT_NOTES)
    )
    notes = result.scalars().all()
    if not notes:
        return {"notes": [], "connections": [], "note_count": 0}

    note_ids = [n.id for n in notes]

    # Mind connections
    conn_result = await db.execute(
        select(MindConnection)
        .where(
            MindConnection.user_id == user_id,
            MindConnection.note_a_id.in_(note_ids),
        )
        .limit(30)
    )
    connections = conn_result.scalars().all()

    # Similarities
    sim_result = await db.execute(
        select(NoteSimilarity)
        .where(NoteSimilarity.note_id.in_(note_ids))
        .limit(20)
    )
    similarities = sim_result.scalars().all()

    # Build context
    notes_ctx = []
    for n in notes:
        content = (n.markdown_content or "")[:settings.INSIGHT_MAX_NOTE_CHARS]
        tags = [t.tag for t in n.tags]
        notes_ctx.append({
            "id": n.id,
            "title": n.title,
            "tags": tags,
            "content": content,
            "created_at": n.created_at.isoformat() if n.created_at else "",
        })

    conn_ctx = []
    for c in connections:
        conn_ctx.append({
            "note_a": c.note_a_id,
            "note_b": c.note_b_id,
            "shared_tags": json.loads(c.shared_tags) if c.shared_tags else [],
            "similarity": round(c.similarity_score, 3),
            "type": c.connection_type,
        })

    sim_ctx = []
    for s in similarities:
        sim_ctx.append({
            "note": s.note_id,
            "similar_to": s.similar_note_id,
            "score": round(s.similarity_score, 3),
        })

    return {
        "notes": notes_ctx,
        "connections": conn_ctx,
        "similarities": sim_ctx,
        "note_count": len(notes_ctx),
        "connection_count": len(conn_ctx),
    }


# ── LLM calls now handled by ai-sdk-python (see llm.py) ──

# PLACEHOLDER_GENERATE

async def generate_insight_report(db: AsyncSession, generation: InsightGeneration) -> None:
    """Generate a full insight report using AI SDK structured output."""
    from app.intelligence.insights.service import broadcast_log
    from app.intelligence.insights.share_cards import build_share_card_payload

    user_id = generation.user_id
    generation_id = generation.id

    await broadcast_log(generation_id, {"type": "starting", "message": "Gathering note context..."})

    ctx = await _fetch_context(db, user_id)
    if not ctx["notes"]:
        raise RuntimeError("Add a few notes before generating insights.")

    await broadcast_log(generation_id, {"type": "progress", "message": f"Analyzing {ctx['note_count']} notes and {ctx['connection_count']} connections..."})

    # Build user prompt
    user_prompt = f"""Analyze these {ctx['note_count']} notes and {ctx['connection_count']} discovered connections:

## Notes
"""
    for n in ctx["notes"]:
        user_prompt += f"\n### {n['title']} (tags: {', '.join(n['tags'])})\n{n['content'][:2000]}\n"

    if ctx["connections"]:
        user_prompt += "\n## Discovered Connections\n"
        for c in ctx["connections"]:
            user_prompt += f"- Notes {c['note_a'][:8]}... ↔ {c['note_b'][:8]}... | shared tags: {c['shared_tags']} | similarity: {c['similarity']} | type: {c['type']}\n"

    if ctx["similarities"]:
        user_prompt += "\n## Semantic Similarities\n"
        for s in ctx["similarities"]:
            user_prompt += f"- {s['note'][:8]}... ~ {s['similar_to'][:8]}... (score: {s['score']})\n"

    user_prompt += f"\nToday's date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
    user_prompt += f"Total notes analyzed: {ctx['note_count']}\nTotal connections: {ctx['connection_count']}\n"

    await broadcast_log(generation_id, {"type": "progress", "message": "Generating report with AI..."})

    # Use AI SDK generate_object for structured output
    report_obj: InsightReportOutput = await generate_report(
        system=REPORT_SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    await broadcast_log(generation_id, {"type": "progress", "message": "Saving report..."})

    # Persist report
    generated_at = datetime.now(timezone.utc)
    report_id = str(uuid.uuid4())

    evidence_items = [ev.model_dump() for ev in report_obj.evidence_items]
    action_items = [act.model_dump() for act in report_obj.action_items]

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

    # Validate note_ids in evidence
    valid_note_ids = {n["id"] for n in ctx["notes"]}
    validated_evidence = []
    for ev in evidence_items:
        nid = ev.get("note_id", "")
        if nid not in valid_note_ids and valid_note_ids:
            nid = next(iter(valid_note_ids))
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
        card_rank=1,
        report_markdown=report_obj.report_markdown,
        report_json=json.dumps(report_dict, ensure_ascii=False),
        source_note_ids=json.dumps([n["id"] for n in ctx["notes"]]),
        generated_at=generated_at,
    ))

    for idx, ev in enumerate(validated_evidence, 1):
        db.add(InsightEvidenceItem(
            id=str(uuid.uuid4()),
            report_id=report_id,
            note_id=ev["note_id"],
            quote=str(ev.get("quote", ""))[:500],
            rationale=str(ev.get("rationale", ""))[:500],
            sort_order=idx,
        ))

    for idx, act in enumerate(action_items, 1):
        db.add(InsightActionItem(
            id=str(uuid.uuid4()),
            report_id=report_id,
            title=str(act.get("title", ""))[:255],
            detail=str(act.get("detail", ""))[:500],
            priority=str(act.get("priority", "medium"))[:16],
            sort_order=idx,
        ))

    generation.status = TaskStatus.COMPLETED
    generation.total_reports = 1
    generation.completed_at = generated_at
    generation.is_active = True
    generation.summary = f"Generated insight report: {report_obj.title}"
    generation.error = None

    await db.commit()
    await broadcast_log(generation_id, {"type": "completed", "summary": generation.summary})


