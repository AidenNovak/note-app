"""Insight tools — unified tool pattern for the Agentic loop."""
from __future__ import annotations

from app.intelligence.insights.tools.base import Tool, ToolContext, ToolResult, insight_tool
from app.intelligence.insights.tools.cluster import cluster_notes_tool
from app.intelligence.insights.tools.discover import discover_angles_tool
from app.intelligence.insights.tools.render import render_share_card_tool
from app.intelligence.insights.tools.write import write_report_tool

__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "insight_tool",
    "cluster_notes_tool",
    "discover_angles_tool",
    "write_report_tool",
    "render_share_card_tool",
    "ALL_TOOLS",
]

ALL_TOOLS: list[Tool] = [
    cluster_notes_tool,
    discover_angles_tool,
    write_report_tool,
    render_share_card_tool,
]
