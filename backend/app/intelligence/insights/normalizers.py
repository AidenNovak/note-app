from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.intelligence.insights.share_cards import build_share_card_payload


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clamp_score(value: object, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(numeric, 0.0), 1.0)


def normalize_reports(
    report_payloads: list[dict[str, object]],
    available_note_ids: set[str],
) -> list[dict[str, object]]:
    normalized_reports: list[dict[str, object]] = []
    now = _utcnow()

    for payload in report_payloads:
        evidence_items: list[dict[str, str]] = []
        for item in payload.get("evidence_items", []):
            if not isinstance(item, dict):
                continue
            note_id = item.get("note_id")
            quote = str(item.get("quote") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            if not isinstance(note_id, str) or note_id not in available_note_ids:
                continue
            if not quote or not rationale:
                continue
            evidence_items.append(
                {
                    "note_id": note_id,
                    "quote": quote,
                    "rationale": rationale,
                }
            )

        source_note_ids = [
            note_id
            for note_id in payload.get("source_note_ids", [])
            if isinstance(note_id, str) and note_id in available_note_ids
        ]
        for evidence in evidence_items:
            if evidence["note_id"] not in source_note_ids:
                source_note_ids.append(evidence["note_id"])

        action_items: list[dict[str, str]] = []
        for item in payload.get("action_items", []):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            detail = str(item.get("detail") or "").strip()
            priority = str(item.get("priority") or "medium").strip().lower()
            if priority not in {"low", "medium", "high"}:
                priority = "medium"
            if not title or not detail:
                continue
            action_items.append(
                {
                    "title": title[:255],
                    "detail": detail,
                    "priority": priority,
                }
            )

        normalized_report = {
            "type": str(payload.get("type") or "report")[:32],
            "status": str(payload.get("status") or "published")[:32],
            "title": str(payload.get("title") or "Insight")[:255],
            "description": str(payload.get("description") or "").strip(),
            "confidence": clamp_score(payload.get("confidence")),
            "importance_score": clamp_score(payload.get("importance_score")),
            "novelty_score": clamp_score(payload.get("novelty_score")),
            "review_summary": str(payload.get("review_summary") or "").strip() or None,
            "report_markdown": str(payload.get("report_markdown") or "").strip(),
            "source_note_ids": source_note_ids,
            "evidence_items": evidence_items,
            "action_items": action_items,
        }
        normalized_report["share_card"] = build_share_card_payload(
            report_type=normalized_report["type"],
            title=normalized_report["title"],
            description=normalized_report["description"],
            confidence=normalized_report["confidence"],
            importance_score=normalized_report["importance_score"],
            novelty_score=normalized_report["novelty_score"],
            generated_at=now,
            review_summary=normalized_report["review_summary"],
            evidence_items=normalized_report["evidence_items"],
            action_items=normalized_report["action_items"],
            raw_share_card=payload.get("share_card")
            if isinstance(payload.get("share_card"), dict)
            else None,
        )
        normalized_reports.append(normalized_report)

    return [
        report
        for report in normalized_reports
        if report["description"]
        and report["report_markdown"]
        and report["source_note_ids"]
        and report["evidence_items"]
    ]


def normalize_agent_runs(agent_runs: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, Any]] = []
    fallback_time = _utcnow()

    for item in agent_runs:
        started_at_raw = item.get("started_at")
        completed_at_raw = item.get("completed_at")
        started_at = parse_datetime(started_at_raw) or fallback_time
        completed_at = parse_datetime(completed_at_raw) or started_at
        normalized.append(
            {
                "agent_name": str(item.get("agent_name") or "agent")[:64],
                "stage": str(item.get("stage") or "analysis")[:64],
                "status": str(item.get("status") or "completed")[:32],
                "session_id": str(item.get("session_id") or "").strip()[:128] or None,
                "model_name": str(item.get("model_name") or "").strip()[:128] or None,
                "duration_ms": parse_int(item.get("duration_ms")),
                "api_duration_ms": parse_int(item.get("api_duration_ms")),
                "total_cost_usd": parse_float(item.get("total_cost_usd")),
                "input_tokens": parse_int(item.get("input_tokens")),
                "output_tokens": parse_int(item.get("output_tokens")),
                "summary": str(item.get("summary") or "").strip() or None,
                "output": item.get("output"),
                "error": str(item.get("error") or "").strip() or None,
                "started_at": started_at,
                "completed_at": completed_at,
            }
        )

    return normalized


def parse_datetime(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def parse_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
