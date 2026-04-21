"""Tool: write_report — two-step per-angle report generation (markdown + metadata extraction)."""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from app.intelligence.insights.llm import (
    extract_report_metadata,
    write_report_markdown,
)
from app.intelligence.insights.schemas_ai import InsightReportOutput
from app.intelligence.insights.tools.base import ToolContext, ToolResult, insight_tool


class WriteReportParams(BaseModel):
    """Parameters for the write_report tool."""

    angle_name: str = Field(description="分析角度名称")
    angle_description: str = Field(description="分析角度描述")
    type_hint: str = Field(description="报告类型: pattern | connection | gap | trend | synthesis")
    notes_content: str = Field(description="相关笔记的完整内容文本")
    note_count: int = Field(description="包含的笔记数量")
    date: str = Field(description="当前日期 YYYY-MM-DD")
    group_index: int = Field(description="角度序号（用于流式事件分组）")
    note_index: list[tuple[str, str]] = Field(default_factory=list, description="笔记 ID → 标题映射列表")


@insight_tool(
    name="write_report",
    description=(
        "为指定的分析角度生成深度中文洞察报告。包含两步："
        "1) 流式生成 Markdown 正文；2) 从正文中提取结构化元数据（标题、证据、行动项、分享卡片）。"
    ),
    params=WriteReportParams,
)
async def write_report_tool(params: WriteReportParams, ctx: ToolContext) -> ToolResult:
    started = time.perf_counter()

    # Step 1: stream markdown
    write_result = await write_report_markdown(
        angle_name=params.angle_name,
        angle_description=params.angle_description,
        type_hint=params.type_hint,
        notes_content=params.notes_content,
        generation_id=ctx.generation_id,
        group_index=params.group_index,
    )

    # Step 2: extract metadata
    extraction = await extract_report_metadata(
        markdown=write_result.text,
        angle_name=params.angle_name,
        type_hint=params.type_hint,
        note_index=params.note_index,
        note_count=params.note_count,
        date=params.date,
    )

    duration_ms = int((time.perf_counter() - started) * 1000)

    report = InsightReportOutput(
        title=extraction.title,
        description=extraction.description,
        type=extraction.type,
        report_markdown=write_result.text,
        thinking_trace=write_result.reasoning or None,
        confidence=extraction.confidence,
        importance_score=extraction.importance_score,
        novelty_score=extraction.novelty_score,
        evidence_items=extraction.evidence_items,
        action_items=extraction.action_items,
        share_card=extraction.share_card,
    )

    # Rough token estimates
    input_tokens = len(params.notes_content) // 4
    output_tokens = (len(write_result.text) + len(extraction.model_dump_json())) // 4

    return ToolResult(
        output=report,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
