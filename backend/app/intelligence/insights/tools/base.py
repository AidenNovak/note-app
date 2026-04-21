"""Tool infrastructure for the Insight Agent.

Inspired by Cloudflare Agents SDK's Unified Tool Pattern. Each tool is a
self-contained unit with a name, description, parameter schema, and handler.
The Agent's planner LLM decides which tool to call based on current state.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ToolContext:
    """Context passed to every tool handler."""

    db: AsyncSession
    user_id: str
    generation_id: str
    agent: Any | None = None  # InsightAgent, circular import avoided via Any


@dataclass
class ToolResult:
    """Result of a tool execution, including telemetry for AgentRun tracking."""

    output: Any
    success: bool = True
    error: str | None = None
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float | None = None


@dataclass
class Tool:
    """A registered insight tool."""

    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]


def insight_tool(
    name: str,
    description: str,
    params: type[BaseModel],
) -> Callable[[Callable[[Any, ToolContext], Awaitable[ToolResult]]], Tool]:
    """Decorator to register an insight tool.

    Example:
        @insight_tool(
            name="cluster_notes",
            description="分析笔记关联图发现主题聚类",
            params=ClusterNotesParams,
        )
        async def cluster_notes_tool(params: ClusterNotesParams, ctx: ToolContext) -> ToolResult:
            ...
    """

    def decorator(
        fn: Callable[[Any, ToolContext], Awaitable[ToolResult]],
    ) -> Tool:
        return Tool(name=name, description=description, parameters=params, handler=fn)

    return decorator
