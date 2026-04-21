"""Tool: render_share_card — generate PNG/HTML share cards from report metadata."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.intelligence.insights.schemas_ai import ShareCardOutput
from app.intelligence.insights.share_cards import build_share_card_payload
from app.intelligence.insights.tools.base import ToolContext, ToolResult, insight_tool


class RenderShareCardParams(BaseModel):
    """Parameters for the render_share_card tool."""

    report_type: str = Field(description="报告类型")
    title: str = Field(description="报告标题")
    description: str = Field(description="报告描述")
    confidence: float = Field(default=0.7)
    importance_score: float = Field(default=0.7)
    novelty_score: float = Field(default=0.5)
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    action_items: list[dict[str, Any]] = Field(default_factory=list)
    raw_share_card: dict[str, Any] | None = Field(default=None)


@insight_tool(
    name="render_share_card",
    description="将报告元数据渲染为杂志风格的分享卡片（PNG/HTML）。",
    params=RenderShareCardParams,
)
async def render_share_card_tool(params: RenderShareCardParams, ctx: ToolContext) -> ToolResult:
    started = time.perf_counter()

    card_payload = build_share_card_payload(
        report_type=params.report_type,
        title=params.title,
        description=params.description,
        confidence=params.confidence,
        importance_score=params.importance_score,
        novelty_score=params.novelty_score,
        generated_at=datetime.now(timezone.utc),
        evidence_items=params.evidence_items,
        action_items=params.action_items,
        raw_share_card=params.raw_share_card,
    )

    duration_ms = int((time.perf_counter() - started) * 1000)

    return ToolResult(
        output=card_payload,
        duration_ms=duration_ms,
    )
