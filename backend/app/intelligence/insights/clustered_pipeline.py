"""Clustered Insight Pipeline — graph-driven multi-angle report generation.

Phase 2 refactor: The pipeline now runs through InsightAgent, which executes
each step via the unified Tool interface. Telemetry is recorded per-tool,
and the architecture is ready for auto mode.

Two-phase approach:
  Phase 1: Graph clustering (Louvain) + LLM angle discovery (~3s)
  Phase 2: Parallel/per-angle report generation via Agent.run_pipeline (~30s)

Produces 3-5 insight reports per run, each from a different analysis angle
discovered from the user's knowledge graph structure.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.intelligence.insights.agent import ExecutionMode, InsightAgent
from app.intelligence.insights.graph_clustering import (
    NoteCluster,
    cluster_notes,
)
from app.intelligence.insights.llm import discover_angles
from app.intelligence.insights.schemas_ai import AngleOutput, InsightReportOutput
from app.intelligence.insights.service import broadcast_log, clear_generation_buffers
from app.models import (
    InsightActionItem,
    InsightEvidenceItem,
    InsightGeneration,
    InsightReport,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# Target 3-5 reports
MIN_ANGLES = 3
MAX_ANGLES = 5
# Max notes sampled for the entire pipeline (keeps cost/latency down)
MAX_NOTES_TOTAL = 30
# Max notes per angle for full-text inclusion
MAX_NOTES_PER_ANGLE = 8
# Max chars of note content per angle (prevent token overflow)
MAX_CONTENT_CHARS_PER_ANGLE = 15000


def _build_fallback_angles(clusters: list[NoteCluster]) -> list[AngleOutput]:
    """Build deterministic analysis angles when LLM angle discovery fails."""
    type_hints = ["pattern", "connection", "trend", "gap", "synthesis"]
    angles: list[AngleOutput] = []

    ranked_clusters = sorted(clusters, key=lambda cluster: len(cluster.note_ids), reverse=True)[:MAX_ANGLES]
    for idx, cluster in enumerate(ranked_clusters):
        top_tags = [tag for tag in cluster.shared_tags if tag][:2]
        angle_name = " / ".join(top_tags) if top_tags else f"主题 {idx + 1}"
        cluster_size = len(cluster.note_ids)
        description = (
            f'梳理围绕"{angle_name}"主题的共同模式、关键张力与下一步行动。'
            if cluster_size > 1
            else f'从"{angle_name}"这条孤立线索出发，提炼它最值得扩展的方向。'
        )
        angles.append(
            AngleOutput(
                angle_name=angle_name[:24],
                description=description,
                note_ids=cluster.note_ids[: max(MAX_NOTES_PER_ANGLE, 5)],
                type_hint=type_hints[idx % len(type_hints)],
            )
        )

    return angles


def _build_cluster_summary(
    clusters: list[NoteCluster],
    notes: list[dict],
) -> str:
    """Build a concise summary of all clusters for the angle-discovery LLM."""
    note_map = {n["id"]: n for n in notes}
    lines = [f"# 知识图谱概览 — {len(notes)} 条笔记, {len(clusters)} 个主题簇\n"]

    for cluster in clusters:
        tags_str = ", ".join(cluster.shared_tags[:5]) if cluster.shared_tags else "无标签"
        lines.append(f"\n## 簇 {cluster.cluster_id}: {tags_str} ({len(cluster.note_ids)} 条笔记)")
        lines.append(f"内部连接数: {len(cluster.internal_connection_ids)}, 平均相似度: {cluster.avg_similarity}")

        # Show titles + first 80 chars of content for each note
        for nid in cluster.note_ids[:15]:  # cap preview at 15 notes per cluster
            note = note_map.get(nid)
            if not note:
                continue
            title = note["title"][:60]
            tags = ", ".join(note["tags"][:3]) if note["tags"] else ""
            preview = note["content"][:80].replace("\n", " ")
            lines.append(f"- [{nid}] {title} | {tags} | {preview}...")

    return "\n".join(lines)


async def run_clustered_pipeline(db: AsyncSession, generation: InsightGeneration) -> None:
    """Main clustered pipeline entry point.

    Phase 1: Graph clustering + angle discovery
    Phase 2: Report generation via InsightAgent.run_pipeline
    Phase 3: Persist results
    """
    user_id = generation.user_id
    generation_id = generation.id
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Create and start the Agent ──
    agent = await InsightAgent.load(generation_id, db)
    agent.mode = ExecutionMode.PIPELINE
    await agent.on_start()

    # ── Phase 1a: Fetch & cluster ──
    await broadcast_log(generation_id, {
        "type": "starting",
        "message": "正在分析知识图谱...",
    })

    clusters, all_notes, note_tags = await cluster_notes(db, user_id)

    # Release the read-only transaction immediately so the connection isn't
    # held idle-in-transaction while we do synchronous data prep.
    await db.rollback()

    if not all_notes:
        await agent.on_finish(status=TaskStatus.FAILED, summary="请先添加一些笔记再生成洞察。")
        raise RuntimeError("请先添加一些笔记再生成洞察。")

    # ── Sample notes if too many (keep cost/latency reasonable) ──
    if len(all_notes) > MAX_NOTES_TOTAL:
        scored = sorted(
            all_notes,
            key=lambda n: (len(n.get("tags", [])), len(n.get("content", ""))),
            reverse=True,
        )
        deterministic_count = int(MAX_NOTES_TOTAL * 0.6)
        pool = scored[deterministic_count:]
        random_pick = random.sample(pool, min(len(pool), MAX_NOTES_TOTAL - deterministic_count))
        all_notes = scored[:deterministic_count] + random_pick
        logger.info("Sampled %d notes from %d total", len(all_notes), len(scored))

    note_map = {n["id"]: n for n in all_notes}

    # Filter cluster note_ids to only include sampled notes
    sampled_ids = set(note_map.keys())
    for cluster in clusters:
        cluster.note_ids = [nid for nid in cluster.note_ids if nid in sampled_ids]
    clusters = [c for c in clusters if c.note_ids]  # drop empty clusters

    await broadcast_log(generation_id, {
        "type": "clustering",
        "cluster_count": len(clusters),
        "note_count": len(all_notes),
        "message": f"发现 {len(clusters)} 个主题簇，共 {len(all_notes)} 条笔记",
    })

    await broadcast_log(generation_id, {
        "type": "decision",
        "stage": "clusters_built",
        "payload": {
            "cluster_count": len(clusters),
            "note_count": len(all_notes),
            "clusters": [
                {
                    "size": len(c.note_ids),
                    "keywords": list(getattr(c, "keywords", []) or [])[:5],
                }
                for c in clusters[:8]
            ],
        },
    })

    # ── Phase 1b: Angle discovery ──
    num_angles = min(MAX_ANGLES, max(MIN_ANGLES, len(clusters)))
    cluster_summary = _build_cluster_summary(clusters, all_notes)

    logger.info("Angle discovery: %d clusters, requesting %d angles", len(clusters), num_angles)

    try:
        angle_result = await discover_angles(
            cluster_summaries=cluster_summary,
            num_angles=num_angles,
        )
        angles = angle_result.angles[:MAX_ANGLES]
    except Exception as exc:
        logger.warning("Angle discovery failed; using deterministic fallback: %s", exc)
        await broadcast_log(generation_id, {
            "type": "progress",
            "message": "AI 角度发现失败，切换到启发式分析...",
        })
        angles = _build_fallback_angles(clusters)

    # Validate note_ids in each angle (remove invalid ones)
    valid_note_ids = set(note_map.keys())
    for angle in angles:
        angle.note_ids = [nid for nid in angle.note_ids if nid in valid_note_ids]

    # Remove angles with no valid notes
    angles = [a for a in angles if a.note_ids]

    if not angles:
        angles = _build_fallback_angles(clusters)
        angles = [a for a in angles if a.note_ids]
    if not angles:
        await agent.on_finish(status=TaskStatus.FAILED, summary="未能发现有效的分析角度。")
        raise RuntimeError("未能发现有效的分析角度。")

    await broadcast_log(generation_id, {
        "type": "agent_turn",
        "turn": 0,
        "groups": [
            {
                "theme": a.angle_name,
                "angle": a.description,
                "count": len(a.note_ids),
                "type_hint": a.type_hint,
            }
            for a in angles
        ],
        "message": f"发现 {len(angles)} 个分析角度",
    })

    await broadcast_log(generation_id, {
        "type": "decision",
        "stage": "angles_picked",
        "payload": {
            "angles": [
                {
                    "angle_name": a.angle_name,
                    "type_hint": a.type_hint,
                    "note_titles": [
                        (note_map[nid].get("title") or nid)[:40]
                        for nid in a.note_ids[:5]
                        if nid in note_map
                    ],
                }
                for a in angles
            ],
        },
    })

    # Persist workspace so the agent can resume after a restart
    agent.workspace["clusters"] = [c.__dict__ if hasattr(c, "__dataclass_fields__") else c for c in clusters]
    agent.workspace["note_map"] = note_map
    agent.workspace["angles"] = [a.model_dump() if hasattr(a, "model_dump") else a for a in angles]
    await agent._persist_workspace()

    # Close the read-only DB transaction before long-running LLM calls so the
    # connection isn't idle-in-transaction while waiting for OpenRouter.
    await db.rollback()

    # ── Phase 2: Report generation via Agent ──
    reports = await agent.run_pipeline(today)

    if not reports:
        await agent.on_finish(status=TaskStatus.FAILED, summary="所有报告生成均失败。")
        raise RuntimeError("所有报告生成均失败。")

    # ── Phase 3: Persist ──
    async with async_session() as persist_db:
        generation_for_persist = await persist_db.get(InsightGeneration, generation_id)
        if generation_for_persist is None:
            raise RuntimeError("Generation not found during persist")
        await _persist_clustered_reports(persist_db, generation_for_persist, reports, all_notes)

    # Notify completion
    summary = f"基于图聚类生成了 {len(reports)} 篇洞察报告，分析了 {len(all_notes)} 条笔记"
    await agent.on_finish(status=TaskStatus.COMPLETED, summary=summary)

    logger.info(
        "Clustered pipeline completed: %d reports, generation=%s",
        len(reports), generation_id,
    )


async def _persist_clustered_reports(
    db: AsyncSession,
    generation: InsightGeneration,
    reports: list[tuple[InsightReportOutput, list[str]]],
    all_notes: list[dict],
) -> None:
    """Persist reports from clustered pipeline to DB."""
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

    for idx, (report_obj, source_note_ids) in enumerate(reports, 1):
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
            if nid not in valid_note_ids and source_note_ids:
                nid = source_note_ids[0]
            validated_evidence.append({**ev, "note_id": nid})

        # Filter source_note_ids
        valid_sources = [nid for nid in source_note_ids if nid in valid_note_ids]

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
            source_note_ids=json.dumps(valid_sources),
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
    generation.workflow_version = "clustered-v2"
    generation.summary = f"基于图聚类生成了 {len(reports)} 篇洞察报告，分析了 {len(all_notes)} 条笔记"
    generation.error = None

    await db.commit()

    completed_event = {
        "type": "completed",
        "summary": generation.summary,
    }
    await broadcast_log(generation_id, completed_event)
    clear_generation_buffers(generation_id)
