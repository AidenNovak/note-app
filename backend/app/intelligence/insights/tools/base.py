"""Tool infrastructure for the Insight Agent.

Aligned with Cloudflare Think's Unified Tool Pattern:
  - Tool           →  self-contained unit with name, description, schema, handler
  - needsApproval  →  tools can require user approval before execution
  - before/after   →  lifecycle hooks for observability and interception
  - ToolContext    →  injected context (db, user_id, generation_id, agent)
  - ToolResult     →  structured result with telemetry

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
    """Context passed to every tool handler.

    Mirrors Cloudflare Think's tool-call context: the handler receives
    the typed params plus runtime context (db session, user, agent).
    """

    db: AsyncSession
    user_id: str
    generation_id: str
    agent: Any | None = None  # InsightAgent, circular import avoided via Any


@dataclass
class ToolResult:
    """Result of a tool execution, including telemetry for AgentRun tracking.

    Aligned with Think's tool result shape:
      output   →  any serializable result
      success  →  boolean
      error    →  error message if failed
      duration_ms / tokens / cost  →  observability
    """

    output: Any
    success: bool = True
    error: str | None = None
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float | None = None


@dataclass
class ToolApprovalRequest:
    """Represents a pending approval for a tool call.

    Think's ``needsApproval`` pattern: when a tool declares it needs approval,
    the agent pauses execution and emits an approval-requested event. The
    client must respond with ``approved: true | false`` before the tool runs.
    """

    tool_name: str
    tool_call_id: str
    input_params: dict[str, Any]
    reason: str | None = None


# Hook signatures (aligned with Think lifecycle hooks)
ToolBeforeHook = Callable[["Tool", BaseModel, ToolContext], Awaitable[None]]
ToolAfterHook = Callable[["Tool", BaseModel, ToolContext, ToolResult], Awaitable[None]]


@dataclass
class Tool:
    """A registered insight tool.

    Aligned with Think's tool definition:
      name            →  how the LLM references the tool
      description     →  shown to the LLM in the system prompt
      parameters      →  Pydantic model for typed arguments
      handler         →  async function(params, ctx) → ToolResult
      needs_approval  →  callable(params) → bool; if True, pauses for approval
    """

    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]
    needs_approval: Callable[[BaseModel], bool] | None = None
    before_hooks: list[ToolBeforeHook] = field(default_factory=list)
    after_hooks: list[ToolAfterHook] = field(default_factory=list)

    async def execute(self, params: BaseModel, ctx: ToolContext) -> ToolResult:
        """Execute the tool with before/after hooks.

        Think-style lifecycle:
          before_hooks(params, ctx)
          → handler(params, ctx)
          → after_hooks(params, ctx, result)
        """
        started = time.perf_counter()

        for hook in self.before_hooks:
            await hook(self, params, ctx)

        try:
            result = await self.handler(params, ctx)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = ToolResult(
                output=None,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )

        # Ensure duration is set even if handler forgot to
        if result.duration_ms == 0:
            result.duration_ms = int((time.perf_counter() - started) * 1000)

        for hook in self.after_hooks:
            await hook(self, params, ctx, result)

        return result

    def check_needs_approval(self, params: BaseModel) -> bool:
        """Check whether this tool call requires user approval."""
        if self.needs_approval is None:
            return False
        try:
            return self.needs_approval(params)
        except Exception:
            return False


def insight_tool(
    name: str,
    description: str,
    params: type[BaseModel],
    needs_approval: Callable[[BaseModel], bool] | None = None,
) -> Callable[[Callable[[Any, ToolContext], Awaitable[ToolResult]]], Tool]:
    """Decorator to register an insight tool.

    Aligned with Think's tool registration pattern. Supports optional
    ``needs_approval`` for human-in-the-loop tool calls.

    Example:
        @insight_tool(
            name="cluster_notes",
            description="分析笔记关联图发现主题聚类",
            params=ClusterNotesParams,
        )
        async def cluster_notes_tool(params: ClusterNotesParams, ctx: ToolContext) -> ToolResult:
            ...

    Example with approval:
        @insight_tool(
            name="delete_notes",
            description="删除笔记",
            params=DeleteNotesParams,
            needs_approval=lambda p: len(p.note_ids) > 1,
        )
        async def delete_notes_tool(params, ctx) -> ToolResult:
            ...
    """

    def decorator(
        fn: Callable[[Any, ToolContext], Awaitable[ToolResult]],
    ) -> Tool:
        return Tool(
            name=name,
            description=description,
            parameters=params,
            handler=fn,
            needs_approval=needs_approval,
        )

    return decorator
