"""Direct OpenRouter-based insight generation with mind connection context.

Replaces the Claude SDK agent approach with a single structured prompt that
produces a 1500-2000 word report with charts/sections.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
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


async def _call_openrouter(system: str, user_msg: str) -> dict:
    """Call OpenRouter chat completion and parse JSON response."""
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 8000,
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]

    # Parse JSON (strip markdown fences if present)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    return json.loads(raw)

async def _call_openrouter_stream(system: str, user_msg: str, generation_id: str) -> dict:
    """Call OpenRouter with streaming, broadcasting chunks via SSE."""
    from app.intelligence.insights.service import broadcast_log

    collected = ""
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 8000,
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        collected += token
                        await broadcast_log(generation_id, {
                            "type": "token",
                            "token": token,
                        })
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

    # Parse collected JSON
    raw = collected.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    return json.loads(raw)

# PLACEHOLDER_GENERATE

async def generate_insight_report(db: AsyncSession, generation: InsightGeneration) -> None:
    """Generate a full insight report and persist it to the database."""
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

    payload = await _call_openrouter_stream(REPORT_SYSTEM_PROMPT, user_prompt, generation_id)

    await broadcast_log(generation_id, {"type": "progress", "message": "Saving report..."})

    # Persist report
    generated_at = datetime.now(timezone.utc)
    report_id = str(uuid.uuid4())

    # Build share card
    evidence_items = payload.get("evidence_items", [])
    action_items = payload.get("action_items", [])

    share_card_data = build_share_card_payload(
        report_type=payload.get("type", "report"),
        title=payload.get("title", "Insight Report"),
        description=payload.get("description", ""),
        confidence=float(payload.get("confidence", 0.7)),
        importance_score=float(payload.get("importance_score", 0.7)),
        novelty_score=float(payload.get("novelty_score", 0.5)),
        generated_at=generated_at,
        evidence_items=evidence_items,
        action_items=action_items,
        raw_share_card=payload.get("share_card"),
    )

    # Validate note_ids in evidence
    valid_note_ids = {n["id"] for n in ctx["notes"]}
    validated_evidence = []
    for ev in evidence_items:
        nid = ev.get("note_id", "")
        if nid not in valid_note_ids and valid_note_ids:
            nid = next(iter(valid_note_ids))
        validated_evidence.append({**ev, "note_id": nid})

    db.add(InsightReport(
        id=report_id,
        generation_id=generation_id,
        user_id=user_id,
        type=payload.get("type", "report"),
        status="published",
        title=payload.get("title", "Insight Report"),
        description=payload.get("description", ""),
        report_version=1,
        confidence=float(payload.get("confidence", 0.7)),
        importance_score=float(payload.get("importance_score", 0.7)),
        novelty_score=float(payload.get("novelty_score", 0.5)),
        review_summary=None,
        card_rank=1,
        report_markdown=payload.get("report_markdown", ""),
        report_json=json.dumps(payload, ensure_ascii=False),
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
    generation.summary = f"Generated insight report: {payload.get('title', 'Report')}"
    generation.error = None

    await db.commit()
    await broadcast_log(generation_id, {"type": "completed", "summary": generation.summary})


