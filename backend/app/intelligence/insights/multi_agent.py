"""Multi-Agent Insight Orchestrator — s0 dispatches groups, sN sub-agents generate insights.

s0 reads the full note manifest, groups notes into sets of ~5 with unique analysis angles,
ensuring every note is selected at least once. Each sub-agent independently generates an
insight report for its assigned group.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    InsightActionItem,
    InsightEvidenceItem,
    InsightGeneration,
    InsightReport,
    TaskStatus,
)
from app.intelligence.insights.workspace_agent import (
    _fetch_all_notes,
    _fetch_connections,
)

logger = logging.getLogger(__name__)

MULTI_AGENT_GROUP_SIZE = 5

# ---------------------------------------------------------------------------
# s0 Orchestrator prompt
# ---------------------------------------------------------------------------

S0_SYSTEM_PROMPT = """\
You are a knowledge workspace orchestrator. Given a manifest of all user notes,
your job is to divide them into groups of approximately {group_size} notes each,
and assign each group a unique analysis angle.

## Requirements

1. EVERY note must appear in at least one group. Some notes may appear in multiple groups.
2. Each group should have approximately {group_size} notes (3-7 is acceptable).
3. Each group must have a unique, interesting analysis angle that discovers hidden connections.
4. Provide diversity in angles — avoid repetitive themes.
5. Try to find surprising, non-obvious connections between notes.

## Output Format

Return ONLY a JSON array of group objects:
```json
[
  {{
    "angle": "A compelling analysis angle in 1-2 sentences",
    "note_ids": ["id1", "id2", "id3", "id4", "id5"],
    "theme": "Short theme label (2-4 words)"
  }}
]
```

Write angles in the SAME LANGUAGE as the user's notes.
"""

# ---------------------------------------------------------------------------
# Sub-agent prompt
# ---------------------------------------------------------------------------

SUB_AGENT_SYSTEM_PROMPT = """\
You are an insight analyst. You have been assigned a specific group of notes and an analysis angle.
Generate a focused insight report based on your assigned angle.

## Output Format

Return ONLY a JSON object:
{{
  "title": "Compelling report title",
  "description": "2-3 sentence executive summary",
  "type": "report",
  "report_markdown": "Full report in markdown. FIRST PARAGRAPH must be a 50-100 word summary. \
Rest is free-form analysis with ## sections. Be thorough but concise.",
  "confidence": 0.0-1.0,
  "importance_score": 0.0-1.0,
  "novelty_score": 0.0-1.0,
  "evidence_items": [
    {{"note_id": "...", "quote": "exact quote from note", "rationale": "why this matters"}}
  ],
  "action_items": [
    {{"title": "Action", "detail": "Steps to take", "priority": "high|medium|low"}}
  ],
  "share_card": {{
    "theme": "report",
    "eyebrow": "INSIGHT REPORT",
    "headline": "≤80 chars headline",
    "summary": "2-3 sentences",
    "highlight": "Most surprising finding",
    "evidence_quote": "Best supporting quote",
    "evidence_source": "Source note title",
    "action_title": "Top recommended action",
    "action_detail": "Brief detail",
    "metrics": [{{"label": "Notes Analyzed", "value": "N"}}],
    "footer": "Generated on {date}"
  }}
}}

Write the report in the SAME LANGUAGE as the notes. The first paragraph of report_markdown \
MUST be a standalone 50-100 word summary.
"""

# PLACEHOLDER_REST_OF_FILE


async def _call_llm(messages: list[dict], generation_id: str, stream_prefix: str = "") -> str:
    """Call OpenRouter and stream tokens."""
    from app.intelligence.insights.service import broadcast_log

    collected = ""
    async with httpx.AsyncClient(timeout=settings.AGENT_REQUEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.AGENT_MODEL,
                "messages": messages,
                "max_tokens": settings.AGENT_MAX_TOKENS_PER_TURN,
                "temperature": 0.7,
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
                            "type": "token", "token": token, "prefix": stream_prefix,
                        })
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
    return collected


def _parse_json_response(text: str) -> object:
    """Extract JSON from LLM response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = cleaned.find(start_char)
        end = cleaned.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
    return json.loads(cleaned)


def _ensure_coverage(groups: list[dict], all_note_ids: set[str]) -> list[dict]:
    """Ensure every note appears in at least one group."""
    covered = set()
    for group in groups:
        covered.update(group.get("note_ids", []))
    uncovered = all_note_ids - covered
    if not uncovered:
        return groups
    groups.append({
        "angle": "Miscellaneous notes that reveal overlooked patterns and hidden themes",
        "note_ids": list(uncovered),
        "theme": "Hidden Patterns",
    })
    return groups

# PLACEHOLDER_MAIN_FUNC


async def run_multi_agent(db: AsyncSession, generation: InsightGeneration) -> None:
    """Main multi-agent orchestration loop."""
    from app.intelligence.insights.service import broadcast_log

    user_id = generation.user_id
    generation_id = generation.id

    await broadcast_log(generation_id, {"type": "starting", "message": "Loading workspace..."})

    all_notes = await _fetch_all_notes(db, user_id)
    if not all_notes:
        raise RuntimeError("Add a few notes before generating insights.")

    note_map = {n["id"]: n for n in all_notes}
    all_note_ids = set(note_map.keys())
    connections = await _fetch_connections(db, user_id, list(all_note_ids))

    await broadcast_log(generation_id, {
        "type": "agent_turn", "turn": 0,
        "notes_read": 0, "notes_total": len(all_notes),
        "message": f"Workspace loaded: {len(all_notes)} notes, {len(connections)} connections",
    })

    # s0 orchestrator — group notes
    num_groups = max(1, math.ceil(len(all_notes) / MULTI_AGENT_GROUP_SIZE))
    manifest_lines = [f"# Note Workspace — {len(all_notes)} notes\n"]
    for i, n in enumerate(all_notes, 1):
        tags = ", ".join(n["tags"]) if n["tags"] else "—"
        manifest_lines.append(f"| {i} | {n['id']} | {n['title'][:60]} | {tags} | {n['word_count']} words |")

    s0_messages = [
        {"role": "system", "content": S0_SYSTEM_PROMPT.format(group_size=MULTI_AGENT_GROUP_SIZE)},
        {"role": "user", "content": "\n".join(manifest_lines) + f"\n\nPlease create approximately {num_groups} groups."},
    ]

    await broadcast_log(generation_id, {"type": "progress", "message": f"s0 orchestrator analyzing {len(all_notes)} notes..."})

    s0_response = await _call_llm(s0_messages, generation_id, stream_prefix="[s0] ")
    groups = _parse_json_response(s0_response)
    if not isinstance(groups, list):
        raise RuntimeError("s0 orchestrator did not return a valid group array")

    groups = _ensure_coverage(groups, all_note_ids)

    await broadcast_log(generation_id, {
        "type": "agent_turn", "turn": 1,
        "notes_read": len(all_notes), "notes_total": len(all_notes),
        "message": f"s0 created {len(groups)} analysis groups",
        "groups": [{"theme": g.get("theme", ""), "angle": g.get("angle", ""), "count": len(g.get("note_ids", []))} for g in groups],
    })

    # Run sub-agents
    reports: list[dict] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for idx, group in enumerate(groups):
        group_num = idx + 1
        angle = group.get("angle", "General analysis")
        theme = group.get("theme", f"Group {group_num}")
        group_note_ids = group.get("note_ids", [])

        await broadcast_log(generation_id, {
            "type": "group_started", "group": group_num, "total_groups": len(groups),
            "theme": theme, "angle": angle, "note_count": len(group_note_ids),
        })

        note_content_parts = []
        for nid in group_note_ids:
            note = note_map.get(nid)
            if note:
                note_content_parts.append(
                    f"### {note['title']} (ID: {nid})\nTags: {', '.join(note['tags']) or '—'}\n\n{note['content']}\n"
                )

        sub_messages = [
            {"role": "system", "content": SUB_AGENT_SYSTEM_PROMPT.format(date=today)},
            {"role": "user", "content": f"## Analysis Angle: {angle}\n\n## Theme: {theme}\n\n## Notes ({len(group_note_ids)} total)\n\n" + "\n---\n".join(note_content_parts)},
        ]

        sub_response = await _call_llm(sub_messages, generation_id, stream_prefix=f"[s{group_num}] ")

        report_payload = {}
        try:
            report_payload = _parse_json_response(sub_response)
            if isinstance(report_payload, dict):
                report_payload["_group_note_ids"] = group_note_ids
                reports.append(report_payload)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Sub-agent s%d failed to produce valid JSON", group_num)

        await broadcast_log(generation_id, {
            "type": "group_completed", "group": group_num, "total_groups": len(groups),
            "theme": theme, "title": report_payload.get("title", "") if isinstance(report_payload, dict) else "",
        })

    if not reports:
        raise RuntimeError("No sub-agents produced valid reports")

    await _persist_multi_reports(db, generation, reports, all_notes)

# PLACEHOLDER_PERSIST


async def _persist_multi_reports(
    db: AsyncSession, generation: InsightGeneration,
    reports: list[dict], all_notes: list[dict],
) -> None:
    """Persist multiple reports from multi-agent generation."""
    from app.intelligence.insights.service import broadcast_log
    from app.intelligence.insights.share_cards import build_share_card_payload

    generation_id = generation.id
    user_id = generation.user_id
    generated_at = datetime.now(timezone.utc)
    valid_note_ids = {n["id"] for n in all_notes}

    await db.execute(
        update(InsightGeneration)
        .where(InsightGeneration.user_id == user_id, InsightGeneration.id != generation.id)
        .values(is_active=False)
    )

    for idx, payload in enumerate(reports, 1):
        report_id = str(uuid.uuid4())
        group_note_ids = payload.pop("_group_note_ids", [])
        evidence_items = payload.get("evidence_items", [])
        action_items = payload.get("action_items", [])

        build_share_card_payload(
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

        validated_evidence = []
        for ev in evidence_items:
            nid = ev.get("note_id", "")
            if nid not in valid_note_ids and group_note_ids:
                nid = group_note_ids[0]
            validated_evidence.append({**ev, "note_id": nid})

        db.add(InsightReport(
            id=report_id, generation_id=generation_id, user_id=user_id,
            type=payload.get("type", "report"), status="published",
            title=payload.get("title", "Insight Report"),
            description=payload.get("description", ""),
            report_version=1,
            confidence=float(payload.get("confidence", 0.7)),
            importance_score=float(payload.get("importance_score", 0.7)),
            novelty_score=float(payload.get("novelty_score", 0.5)),
            review_summary=None, card_rank=idx,
            report_markdown=payload.get("report_markdown", ""),
            report_json=json.dumps(payload, ensure_ascii=False),
            source_note_ids=json.dumps(group_note_ids),
            generated_at=generated_at,
        ))

        for ev_idx, ev in enumerate(validated_evidence, 1):
            db.add(InsightEvidenceItem(
                id=str(uuid.uuid4()), report_id=report_id, note_id=ev["note_id"],
                quote=str(ev.get("quote", ""))[:500],
                rationale=str(ev.get("rationale", ""))[:500],
                sort_order=ev_idx,
            ))

        for act_idx, act in enumerate(action_items, 1):
            db.add(InsightActionItem(
                id=str(uuid.uuid4()), report_id=report_id,
                title=str(act.get("title", ""))[:255],
                detail=str(act.get("detail", ""))[:500],
                priority=str(act.get("priority", "medium"))[:16],
                sort_order=act_idx,
            ))

    generation.status = TaskStatus.COMPLETED
    generation.total_reports = len(reports)
    generation.completed_at = generated_at
    generation.is_active = True
    generation.workflow_version = "multi-agent-v1"
    generation.summary = f"Generated {len(reports)} insight reports from {len(reports)} analysis groups"
    generation.error = None

    await db.commit()
    await broadcast_log(generation_id, {"type": "completed", "summary": generation.summary})
