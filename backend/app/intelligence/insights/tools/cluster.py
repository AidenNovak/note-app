"""Tool: cluster_notes — graph-based note clustering via Louvain community detection."""
from __future__ import annotations

import time

from pydantic import BaseModel

from app.intelligence.insights.graph_clustering import cluster_notes
from app.intelligence.insights.tools.base import ToolContext, ToolResult, insight_tool


class ClusterNotesParams(BaseModel):
    """Parameters for the cluster_notes tool."""

    pass  # No parameters needed; operates on the user's full note graph


@insight_tool(
    name="cluster_notes",
    description=(
        "分析用户的笔记关联图（MindConnection），使用 Louvain 社区检测算法 "
        "发现主题聚类。返回若干 NoteCluster，每个聚类包含相关笔记 ID、"
        "共享标签、内部连接和平均相似度。"
    ),
    params=ClusterNotesParams,
)
async def cluster_notes_tool(params: ClusterNotesParams, ctx: ToolContext) -> ToolResult:
    started = time.perf_counter()
    clusters, all_notes, note_tags = await cluster_notes(ctx.db, ctx.user_id)
    duration_ms = int((time.perf_counter() - started) * 1000)

    return ToolResult(
        output={
            "clusters": clusters,
            "all_notes": all_notes,
            "note_tags": note_tags,
        },
        duration_ms=duration_ms,
    )
