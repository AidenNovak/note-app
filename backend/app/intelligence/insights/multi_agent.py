"""Multi-Agent Insight Orchestrator — s0 dispatches groups, sN sub-agents generate insights.

s0 reads the full note manifest, groups notes into sets of ~5 with unique analysis angles,
ensuring every note is selected at least once. Each sub-agent independently generates an
insight report for its assigned group.

Uses ai-sdk-python for structured output (generate_object) and streaming.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone

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
from app.intelligence.insights.llm import get_agent_model, generate_groups, generate_report
from app.intelligence.insights.schemas_ai import InsightReportOutput, NoteGroupOutput
from app.intelligence.insights.workspace_agent import (
    _fetch_all_notes,
    _fetch_connections,
)

logger = logging.getLogger(__name__)

TARGET_REPORTS = 4  # aim for 3-5 reports regardless of note count
S0_MAX_NOTES = 80
S0_MAX_TOKENS = 4096
MAX_GROUPS = 5

# ---------------------------------------------------------------------------
# s0 Orchestrator prompt
# ---------------------------------------------------------------------------

S0_SYSTEM_PROMPT = """\
You are a knowledge workspace orchestrator. Given a manifest of all user notes,
your job is to divide them into exactly {num_groups} thematic groups and assign each group
a unique analysis angle.

## Requirements

1. Create exactly {num_groups} groups — no more, no less.
2. Each group should contain roughly {group_size} notes (±50% is fine).
3. EVERY note must appear in at least one group. Some notes may appear in multiple groups.
4. Each group must have a unique, interesting analysis angle that discovers hidden connections.
5. Provide diversity in angles — avoid repetitive themes.
6. Try to find surprising, non-obvious connections between notes.

## Output Format

Return ONLY a JSON array of group objects:
```json
[
  {{
    "angle": "A compelling analysis angle in 1-2 sentences",
    "note_ids": ["id1", "id2", ...],
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


# ── LLM calls are now via ai-sdk-python (see llm.py) ──


def _ensure_coverage(groups: list[NoteGroupOutput], all_note_ids: set[str]) -> list[NoteGroupOutput]:
    """Ensure every note appears in at least one group."""
    covered = set()
    for group in groups:
        covered.update(group.note_ids)
    uncovered = all_note_ids - covered
    if not uncovered:
        return groups
    groups.append(NoteGroupOutput(
        angle="Miscellaneous notes that reveal overlooked patterns and hidden themes",
        note_ids=list(uncovered),
        theme="Hidden Patterns",
    ))
    return groups

# PLACEHOLDER_MAIN_FUNC


async def run_multi_agent(db: AsyncSession, generation: InsightGeneration) -> None:
    """Main multi-agent orchestration loop using AI SDK structured output."""
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

    # ── s0 orchestrator: group notes via generate_object ──
    s0_notes = all_notes[:S0_MAX_NOTES] if len(all_notes) > S0_MAX_NOTES else all_notes
    s0_note_ids = {n["id"] for n in s0_notes}
    num_groups = min(MAX_GROUPS, max(3, TARGET_REPORTS))
    group_size = max(3, math.ceil(len(s0_notes) / num_groups))

    manifest_lines = [f"# Note Workspace — {len(s0_notes)} notes\n"]
    for i, n in enumerate(s0_notes, 1):
        tags = ", ".join(n["tags"]) if n["tags"] else "—"
        manifest_lines.append(f"| {i} | {n['id']} | {n['title'][:60]} | {tags} | {n['word_count']} words |")

    await broadcast_log(generation_id, {"type": "progress", "message": f"s0 orchestrator analyzing {len(s0_notes)} notes..."})

    s0_system = S0_SYSTEM_PROMPT.format(num_groups=num_groups, group_size=group_size)
    s0_user = "\n".join(manifest_lines) + f"\n\nPlease create exactly {num_groups} groups."

    group_list = await generate_groups(system=s0_system, user_prompt=s0_user)
    groups = list(group_list.groups)
    logger.info("s0 returned %d groups", len(groups))

    groups = _ensure_coverage(groups, s0_note_ids)
    if len(groups) > MAX_GROUPS:
        groups = groups[:MAX_GROUPS]

    await broadcast_log(generation_id, {
        "type": "agent_turn", "turn": 1,
        "notes_read": len(all_notes), "notes_total": len(all_notes),
        "message": f"s0 created {len(groups)} analysis groups",
        "groups": [{"theme": g.theme, "angle": g.angle, "count": len(g.note_ids)} for g in groups],
    })

    # ── Run sub-agents via generate_object for structured reports ──
    reports: list[tuple[InsightReportOutput, list[str]]] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for idx, group in enumerate(groups):
        group_num = idx + 1
        angle = group.angle
        theme = group.theme
        group_note_ids = group.note_ids

        await broadcast_log(generation_id, {
            "type": "group_started", "group": group_num, "total_groups": len(groups),
            "theme": theme, "angle": angle, "note_count": len(group_note_ids),
        })

        try:
            note_content_parts = []
            for nid in group_note_ids:
                note = note_map.get(nid)
                if note:
                    note_content_parts.append(
                        f"### {note['title']} (ID: {nid})\nTags: {', '.join(note['tags']) or '—'}\n\n{note['content']}\n"
                    )

            sub_system = SUB_AGENT_SYSTEM_PROMPT.format(date=today)
            sub_user = (
                f"## Analysis Angle: {angle}\n\n## Theme: {theme}\n\n"
                f"## Notes ({len(group_note_ids)} total)\n\n"
                + "\n---\n".join(note_content_parts)
            )

            report_obj = await generate_report(system=sub_system, user_prompt=sub_user)
            reports.append((report_obj, group_note_ids))

            await broadcast_log(generation_id, {
                "type": "group_completed", "group": group_num, "total_groups": len(groups),
                "theme": theme,
                "title": report_obj.title,
                "description": report_obj.description,
            })
        except Exception as sub_err:
            logger.warning("Sub-agent s%d failed: %s", group_num, sub_err)
            await broadcast_log(generation_id, {
                "type": "group_completed", "group": group_num, "total_groups": len(groups),
                "theme": theme, "title": "", "description": "",
            })

    if not reports:
        raise RuntimeError("No sub-agents produced valid reports")

    await _persist_multi_reports(db, generation, reports, all_notes)

# PLACEHOLDER_PERSIST


async def _persist_multi_reports(
    db: AsyncSession, generation: InsightGeneration,
    reports: list[tuple[InsightReportOutput, list[str]]], all_notes: list[dict],
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

    for idx, (report_obj, group_note_ids) in enumerate(reports, 1):
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

        validated_evidence = []
        for ev in evidence_items:
            nid = ev.get("note_id", "")
            if nid not in valid_note_ids and group_note_ids:
                nid = group_note_ids[0]
            validated_evidence.append({**ev, "note_id": nid})

        report_dict = report_obj.model_dump()
        db.add(InsightReport(
            id=report_id, generation_id=generation_id, user_id=user_id,
            type=report_obj.type, status="published",
            title=report_obj.title,
            description=report_obj.description,
            report_version=1,
            confidence=report_obj.confidence,
            importance_score=report_obj.importance_score,
            novelty_score=report_obj.novelty_score,
            review_summary=None, card_rank=idx,
            report_markdown=report_obj.report_markdown,
            report_json=json.dumps(report_dict, ensure_ascii=False),
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
    generation.workflow_version = "multi-agent-v2"
    generation.summary = f"Generated {len(reports)} insight reports from {len(reports)} analysis groups"
    generation.error = None

    await db.commit()
    await broadcast_log(generation_id, {"type": "completed", "summary": generation.summary})
