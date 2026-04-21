"""Tool: discover_angles — LLM-based analysis angle discovery from clusters."""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from app.intelligence.insights.llm import discover_angles
from app.intelligence.insights.schemas_ai import AngleListOutput
from app.intelligence.insights.tools.base import ToolContext, ToolResult, insight_tool


class DiscoverAnglesParams(BaseModel):
    """Parameters for the discover_angles tool."""

    cluster_summaries: str = Field(description="笔记聚类的文本摘要")
    num_angles: int = Field(default=4, ge=1, le=8, description="期望发现的角度数量")


@insight_tool(
    name="discover_angles",
    description=(
        "基于笔记聚类摘要，使用 LLM 发现 3-5 个有价值的、非显而易见的分析角度。"
        "每个角度包含名称、描述、相关笔记 ID 列表和类型提示（pattern/connection/gap/trend/synthesis）。"
    ),
    params=DiscoverAnglesParams,
)
async def discover_angles_tool(params: DiscoverAnglesParams, ctx: ToolContext) -> ToolResult:
    started = time.perf_counter()
    result = await discover_angles(
        cluster_summaries=params.cluster_summaries,
        num_angles=params.num_angles,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    # Estimate tokens from text length (rough heuristic)
    input_tokens = len(params.cluster_summaries) // 4
    output_tokens = len(result.model_dump_json()) // 4

    return ToolResult(
        output=result,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
