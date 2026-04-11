"""Workspace Agent — multi-turn agent loop that reads ALL notes before generating insight.

Uses OpenRouter API with tool_call XML parsing. The AI receives a manifest of all notes,
reads them in batches via read_notes tool, then calls finish with the final report.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
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
    TaskStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for the workspace agent
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """\
You are an expert knowledge analyst with access to a user's entire note workspace.
Your job is to read ALL notes, analyze patterns, connections, and themes, then produce
a comprehensive insight report.

## Available Tools

You have two tools. Call them by outputting XML tags:

### read_notes
Read a batch of notes by their IDs. Call this multiple times to read all notes.
```
<tool_call>{"tool": "read_notes", "note_ids": ["id1", "id2", ...]}</tool_call>
```
- Read 5-10 notes per batch for efficiency.
- You MUST read every single note before calling finish.

### finish
Submit your final insight report. The system will reject this if you haven't read all notes.
```
<tool_call>{"tool": "finish", "report": { ... }}</tool_call>
```

The report object must follow this JSON schema:
{
  "title": "Report title (compelling, conclusion-driven)",
  "description": "2-3 sentence executive summary",
  "type": "report",
  "report_markdown": "Full markdown report, 1500-2000 words with ## sections",
  "confidence": 0.0-1.0,
  "importance_score": 0.0-1.0,
  "novelty_score": 0.0-1.0,
  "evidence_items": [
    {"note_id": "...", "quote": "exact quote", "rationale": "why this matters"}
  ],
  "action_items": [
    {"title": "Action", "detail": "Steps", "priority": "high|medium|low"}
  ],
  "share_card": {
    "theme": "report",
    "eyebrow": "INSIGHT REPORT",
    "headline": "≤80 chars",
    "summary": "2-3 sentences",
    "highlight": "Most surprising finding",
    "evidence_quote": "Best quote",
    "evidence_source": "Source note title",
    "action_title": "Top action",
    "action_detail": "Brief detail",
    "metrics": [{"label": "Notes Analyzed", "value": "N"}, {"label": "Connections", "value": "N"}],
    "footer": "Generated on YYYY-MM-DD"
  }
}

## Workflow

1. Review the manifest of all notes (titles, tags, word counts).
2. Plan your reading order — group by theme or read chronologically.
3. Call read_notes in batches until you've read every note.
4. After reading all notes, synthesize and call finish with your report.

Write the report in the SAME LANGUAGE as the user's notes. If notes are in Chinese, \
write the entire report in Chinese. If mixed, prefer Chinese.

The report_markdown MUST be 1500-2000 words. Be thorough and analytical.
Include specific quotes and references to actual note content.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_all_notes(db: AsyncSession, user_id: str) -> list[dict]:
    """Fetch ALL notes for a user (no limit)."""
    result = await db.execute(
        select(Note)
        .options(selectinload(Note.tags))
        .where(Note.user_id == user_id)
        .order_by(Note.updated_at.desc())
    )
    notes = result.scalars().all()
    out = []
    for n in notes:
        out.append({
            "id": n.id,
            "title": n.title or "(untitled)",
            "tags": [t.tag for t in n.tags],
            "content": n.markdown_content or "",
            "word_count": len((n.markdown_content or "").split()),
            "created_at": n.created_at.isoformat() if n.created_at else "",
        })
    return out


async def _fetch_connections(db: AsyncSession, user_id: str, note_ids: list[str]) -> list[dict]:
    """Fetch mind connections for context."""
    if not note_ids:
        return []
    conn_result = await db.execute(
        select(MindConnection)
        .where(MindConnection.user_id == user_id, MindConnection.note_a_id.in_(note_ids))
        .limit(50)
    )
    connections = conn_result.scalars().all()
    return [
        {
            "note_a": c.note_a_id,
            "note_b": c.note_b_id,
            "shared_tags": json.loads(c.shared_tags) if c.shared_tags else [],
            "similarity": round(c.similarity_score, 3),
            "type": c.connection_type,
        }
        for c in connections
    ]


def _build_manifest_message(notes: list[dict], connections: list[dict]) -> str:
    """Build the initial manifest message showing all notes metadata."""
    lines = [
        f"# Note Workspace — {len(notes)} notes total\n",
        "| # | ID | Title | Tags | Words |",
        "|---|-----|-------|------|-------|",
    ]
    for i, n in enumerate(notes, 1):
        tags = ", ".join(n["tags"]) if n["tags"] else "—"
        lines.append(f"| {i} | {n['id']} | {n['title'][:50]} | {tags} | {n['word_count']} |")

    if connections:
        lines.append(f"\n## {len(connections)} Discovered Connections")
        for c in connections[:20]:
            lines.append(f"- {c['note_a']} ↔ {c['note_b']} (similarity: {c['similarity']}, type: {c['type']})")

    lines.append(f"\nToday: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append("\nPlease read all notes using read_notes, then call finish with your report.")
    return "\n".join(lines)


_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def _extract_tool_calls(text: str) -> list[dict]:
    """Extract tool_call JSON blocks from assistant text."""
    calls = []
    for match in _TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            calls.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool_call: %s", raw[:200])
    return calls



async def _call_openrouter_agent(
    messages: list[dict], generation_id: str
) -> str:
    """Call OpenRouter with streaming, broadcast tokens, return full text."""
    from app.intelligence.insights.service import broadcast_log

    collected = ""
    async with httpx.AsyncClient(timeout=settings.AGENT_REQUEST_TIMEOUT, verify=False) as client:
        async with client.stream(
            "POST",
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
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
                            "type": "token",
                            "token": token,
                        })
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
    return collected


def _maybe_compress_history(messages: list[dict]) -> list[dict]:
    """If total character count exceeds 400K, compress middle messages."""
    total = sum(len(m.get("content", "")) for m in messages)
    if total < 400_000:
        return messages
    # Keep system, first user, last 4 messages; summarize the rest
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    if len(rest) <= 5:
        return messages
    kept_start = rest[:1]
    kept_end = rest[-4:]
    middle = rest[1:-4]
    summary = f"[Compressed {len(middle)} earlier messages. The agent has been reading notes and analyzing content.]"
    return system + kept_start + [{"role": "user", "content": summary}] + kept_end



async def run_workspace_agent(db: AsyncSession, generation: InsightGeneration) -> None:
    """Main workspace agent loop. Reads all notes via multi-turn conversation."""
    from app.intelligence.insights.service import broadcast_log
    from app.intelligence.insights.share_cards import build_share_card_payload

    user_id = generation.user_id
    generation_id = generation.id

    await broadcast_log(generation_id, {"type": "starting", "message": "Loading workspace..."})

    # 1. Fetch all notes
    all_notes = await _fetch_all_notes(db, user_id)
    if not all_notes:
        raise RuntimeError("Add a few notes before generating insights.")

    note_map = {n["id"]: n for n in all_notes}
    all_note_ids = set(note_map.keys())
    read_note_ids: set[str] = set()
    notes_total = len(all_notes)

    # Fetch connections for context
    connections = await _fetch_connections(db, user_id, list(all_note_ids))

    await broadcast_log(generation_id, {
        "type": "agent_turn",
        "turn": 0,
        "notes_read": 0,
        "notes_total": notes_total,
        "message": f"Workspace loaded: {notes_total} notes, {len(connections)} connections",
    })

    # 2. Build initial messages
    manifest = _build_manifest_message(all_notes, connections)
    messages: list[dict] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": manifest},
    ]

    # 3. Agent loop
    max_turns = settings.AGENT_MAX_TURNS
    for turn in range(1, max_turns + 1):
        messages = _maybe_compress_history(messages)

        assistant_text = await _call_openrouter_agent(messages, generation_id)
        messages.append({"role": "assistant", "content": assistant_text})

        tool_calls = _extract_tool_calls(assistant_text)
        if not tool_calls:
            # No tool calls — AI might be thinking. Nudge it.
            unread = all_note_ids - read_note_ids
            if unread:
                nudge = f"You still have {len(unread)} unread notes. Please call read_notes to continue."
                messages.append({"role": "user", "content": nudge})
                await broadcast_log(generation_id, {
                    "type": "agent_turn",
                    "turn": turn,
                    "notes_read": len(read_note_ids),
                    "notes_total": notes_total,
                    "message": f"Turn {turn}: nudging agent ({len(unread)} unread)",
                })
                continue
            # All read but no finish call — nudge to finish
            messages.append({"role": "user", "content": "All notes have been read. Please call finish with your report now."})
            continue

        # Process tool calls
        tool_results: list[str] = []
        for tc in tool_calls:
            tool_name = tc.get("tool", "")

            if tool_name == "read_notes":
                requested_ids = tc.get("note_ids", [])
                batch_content: list[str] = []
                batch_titles: list[str] = []
                for nid in requested_ids:
                    note = note_map.get(nid)
                    if note:
                        read_note_ids.add(nid)
                        batch_titles.append(note["title"])
                        batch_content.append(
                            f"### {note['title']} (ID: {nid})\n"
                            f"Tags: {', '.join(note['tags']) or '—'}\n"
                            f"Created: {note['created_at']}\n\n"
                            f"{note['content']}\n"
                        )
                    else:
                        batch_content.append(f"[Note {nid} not found]")
                tool_results.append(
                    f"## read_notes result ({len(requested_ids)} notes)\n\n"
                    + "\n---\n".join(batch_content)
                )
                await broadcast_log(generation_id, {
                    "type": "agent_turn",
                    "turn": turn,
                    "notes_read": len(read_note_ids),
                    "notes_total": notes_total,
                    "notes_batch": batch_titles,
                    "message": f"Reading: {', '.join(batch_titles)}",
                })

            elif tool_name == "finish":
                # Check all notes read
                unread = all_note_ids - read_note_ids
                if unread and len(unread) > 0:
                    tool_results.append(
                        f"REJECTED: You have {len(unread)} unread notes. "
                        f"Please read them first: {json.dumps(list(unread)[:20])}"
                    )
                    await broadcast_log(generation_id, {
                        "type": "agent_turn",
                        "turn": turn,
                        "notes_read": len(read_note_ids),
                        "notes_total": notes_total,
                        "message": f"Still have {len(unread)} notes to read...",
                    })
                else:
                    # Accept the report
                    report_payload = tc.get("report", {})
                    await _persist_report(db, generation, report_payload, all_notes, connections)
                    return
            else:
                tool_results.append(f"Unknown tool: {tool_name}")

        # Inject tool results as user message
        if tool_results:
            unread = all_note_ids - read_note_ids
            status_line = f"\n\n[Status: {len(read_note_ids)}/{notes_total} notes read"
            if not unread:
                status_line += " — ALL NOTES READ. Please call finish now.]"
            else:
                status_line += f", {len(unread)} remaining]"
            messages.append({"role": "user", "content": "\n\n".join(tool_results) + status_line})

    # Exhausted turns
    raise RuntimeError(f"Agent did not finish within {max_turns} turns (read {len(read_note_ids)}/{notes_total} notes).")



async def _persist_report(
    db: AsyncSession,
    generation: InsightGeneration,
    payload: dict,
    all_notes: list[dict],
    connections: list[dict],
) -> None:
    """Persist the agent's final report to the database."""
    from app.intelligence.insights.service import broadcast_log
    from app.intelligence.insights.share_cards import build_share_card_payload

    generation_id = generation.id
    user_id = generation.user_id
    generated_at = datetime.now(timezone.utc)
    report_id = str(uuid.uuid4())

    evidence_items = payload.get("evidence_items", [])
    action_items = payload.get("action_items", [])

    # Build share card
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
    valid_note_ids = {n["id"] for n in all_notes}
    validated_evidence = []
    for ev in evidence_items:
        nid = ev.get("note_id", "")
        if nid not in valid_note_ids and valid_note_ids:
            nid = next(iter(valid_note_ids))
        validated_evidence.append({**ev, "note_id": nid})

    # Deactivate previous generations
    await db.execute(
        update(InsightGeneration)
        .where(
            InsightGeneration.user_id == user_id,
            InsightGeneration.id != generation.id,
        )
        .values(is_active=False)
    )

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
        source_note_ids=json.dumps([n["id"] for n in all_notes]),
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
    generation.workflow_version = "workspace-agent-v1"
    generation.summary = f"Generated insight report: {payload.get('title', 'Report')} (analyzed {len(all_notes)} notes)"
    generation.error = None

    await db.commit()
    await broadcast_log(generation_id, {"type": "completed", "summary": generation.summary})
