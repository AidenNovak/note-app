"""InsightAgent — Think-aligned stateful agent for insight generation.

Architecturally aligned with Cloudflare's Think base class:
  - configure_session()    →  sets up context blocks (soul, memory, workspace)
  - before_turn()          →  called before each chat turn
  - after_turn()           →  called after each chat turn
  - before_tool_call()     →  called before tool execution
  - after_tool_call()      →  called after tool execution
  - get_tools()            →  returns the tool registry
  - broadcast()            →  persists events to the event store

Unlike Think, this runs on FastAPI + SQLAlchemy (not Durable Objects),
so workspace persistence is explicit via _persist_workspace().
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.intelligence.insights.event_store import (
    append_event,
    clear_buffers,
    flush_events,
)
from app.intelligence.insights.tools import ALL_TOOLS
from app.intelligence.insights.tools.base import (
    Tool,
    ToolApprovalRequest,
    ToolContext,
    ToolResult,
)
from app.models import InsightGeneration, TaskStatus

logger = logging.getLogger(__name__)


# ── Think-aligned context primitives ──


@dataclass
class ContextBlock:
    """A named context block, mirroring Think's ``withContext`` API.

    Think uses context blocks to assemble the system prompt and give the
    model persistent memory. Each block has:
      label        →  namespace (e.g. "soul", "memory", "workspace")
      description  →  shown to the model (for writable blocks)
      content      →  current content
      max_tokens   →  budget hint (not enforced here)
      writable     →  whether the model can update this block
    """

    label: str
    description: str = ""
    content: str = ""
    max_tokens: int = 0
    writable: bool = False


@dataclass
class Session:
    """Agent session state, aligned with Think's Session abstraction.

    Holds context blocks and conversation history. The agent assembles the
    full prompt from blocks + history on every turn.
    """

    blocks: dict[str, ContextBlock] = field(default_factory=dict)
    conversation: list[dict[str, Any]] = field(default_factory=list)
    max_conversation_messages: int = 40

    def with_context(
        self,
        label: str,
        description: str = "",
        content: str = "",
        max_tokens: int = 0,
        writable: bool = False,
    ) -> "Session":
        """Add or replace a context block (fluent API)."""
        self.blocks[label] = ContextBlock(
            label=label,
            description=description,
            content=content,
            max_tokens=max_tokens,
            writable=writable,
        )
        return self

    def get_block(self, label: str) -> ContextBlock | None:
        return self.blocks.get(label)

    def update_block(self, label: str, content: str) -> None:
        if label in self.blocks:
            self.blocks[label].content = content

    def add_message(self, role: str, content: str) -> None:
        self.conversation.append({
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        # Trim to max length
        if len(self.conversation) > self.max_conversation_messages:
            self.conversation = self.conversation[-self.max_conversation_messages :]

    def build_prompt(self) -> str:
        """Assemble system prompt from context blocks."""
        parts: list[str] = []
        for block in self.blocks.values():
            header = f"### {block.label}"
            if block.description:
                header += f"\n{block.description}"
            parts.append(f"{header}\n\n{block.content}")
        return "\n\n---\n\n".join(parts)


@dataclass
class TurnContext:
    """Context for a single agent turn, mirroring Think's TurnContext.

    Passed to ``before_turn`` and ``after_turn`` hooks.
    """

    user_message: str
    intent: str | None = None
    tools_available: list[str] = field(default_factory=list)
    continuation: bool = False


@dataclass
class TurnConfig:
    """Optional overrides for a turn, mirroring Think's TurnConfig.

    ``before_turn`` can return a TurnConfig to change behaviour for this
    turn only.
    """

    active_tools: list[str] | None = None
    system_prompt_addendum: str | None = None
    max_steps: int | None = None


# ── Agent ──


class InsightAgent:
    """Think-aligned lightweight insight agent.

    Usage (generation):
        agent = InsightAgent(generation_id, user_id, db)
        await agent.on_start()
        # ... pipeline runs directly in pipeline.py ...
        await agent.on_finish(status, summary)

    Usage (chat after generation):
        agent = await InsightAgent.load(generation_id, db)
        await agent.on_chat_message(message)

    Hooks (override in subclasses or monkey-patch):
        configure_session(session)  →  add context blocks
        before_turn(ctx)            →  inspect / mutate turn context
        after_turn(ctx)             →  log / analyse completed turn
        before_tool_call(tool, params, ctx)
        after_tool_call(tool, params, ctx, result)
    """

    max_steps: int = 10

    def __init__(
        self,
        generation_id: str,
        user_id: str,
        db: AsyncSession,
    ):
        self.generation_id = generation_id
        self.user_id = user_id
        self.db = db
        self._tools: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}
        self._sequence = 0

        # Think-style session (assembled from workspace on restore)
        self.session = Session()

        # Legacy workspace dict — kept for backward compat.
        # New code should prefer self.session.blocks[label].content
        self.workspace: dict[str, Any] = {}

    @classmethod
    async def load(cls, generation_id: str, db: AsyncSession) -> "InsightAgent":
        """Restore an agent from the database (workspace + session)."""
        generation = await db.get(InsightGeneration, generation_id)
        if generation is None:
            raise ValueError(f"Generation {generation_id} not found")

        agent = cls(
            generation_id=generation_id,
            user_id=generation.user_id,
            db=db,
        )
        await agent._restore_workspace()
        return agent

    # ── Configuration (Think-style) ──

    def configure_session(self, session: Session) -> Session:
        """Configure the agent's session with context blocks.

        Override this method (or monkey-patch) to add custom blocks.
        Default blocks: soul, memory, workspace.

        Mirrors Think's ``configureSession`` hook.
        """
        persona = (
            "You are a capable insight analyst. You synthesise personal notes "
            "into meaningful reports, spot patterns, connections, and trends. "
            "You are concise, evidence-based, and always ground claims in the "
            "user's own writing."
        )

        session.with_context(
            "soul",
            description="Your core identity and operating principles.",
            content=persona,
            writable=False,
        ).with_context(
            "memory",
            description=(
                "Key facts about the user, their preferences, project context, "
                "and decisions made during conversation. Update when you learn "
                "something useful for future turns."
            ),
            content=self.workspace.get("memory", ""),
            max_tokens=2000,
            writable=True,
        ).with_context(
            "workspace",
            description="Current workspace state (reports, notes, conversation).",
            content=json.dumps(self.workspace, ensure_ascii=False, default=str),
            writable=False,
        )
        return session

    def get_tools(self) -> dict[str, Tool]:
        """Return the tool registry.

        Mirrors Think's ``getTools()`` method. Override to add, remove, or
        filter tools dynamically.
        """
        return self._tools

    # ── Lifecycle ──

    async def on_start(self) -> None:
        """Called when generation begins."""
        await self._restore_workspace()
        self.session = self.configure_session(self.session)
        await self.broadcast({
            "type": "starting",
            "message": "Insight Agent 启动...",
        })

    async def on_finish(self, status: TaskStatus = TaskStatus.COMPLETED, summary: str = "") -> None:
        """Called when generation completes or fails."""
        if status == TaskStatus.COMPLETED:
            await self.broadcast({
                "type": "completed",
                "summary": summary or "洞察分析完成",
            })
        else:
            await self.broadcast({
                "type": "error",
                "message": summary or "洞察分析失败",
            })
        await self._persist_workspace()
        await flush_events(self.db, self.generation_id)
        clear_buffers(self.generation_id)

    # ── Turn hooks (Think-style) ──

    async def before_turn(self, ctx: TurnContext) -> TurnConfig | None:
        """Hook called before each chat turn.

        Mirrors Think's ``beforeTurn`` lifecycle hook. Return a TurnConfig
        to override tools, prompt, or max_steps for this turn only.
        """
        logger.debug(
            "Turn starting for generation=%s: intent=%s tools=%s",
            self.generation_id,
            ctx.intent,
            ctx.tools_available,
        )
        return None

    async def after_turn(self, ctx: TurnContext) -> None:
        """Hook called after each chat turn completes.

        Mirrors Think's ``onChatResponse`` / ``after_turn`` hook.
        """
        logger.debug(
            "Turn finished for generation=%s: intent=%s",
            self.generation_id,
            ctx.intent,
        )

    async def before_tool_call(self, tool: Tool, params: BaseModel, ctx: ToolContext) -> None:
        """Hook called before a tool is executed.

        Mirrors Think's ``beforeToolCall`` hook.
        """
        await self.broadcast({
            "type": "tool_start",
            "tool": tool.name,
            "input": params.model_dump() if hasattr(params, "model_dump") else dict(params),
        })

    async def after_tool_call(
        self, tool: Tool, params: BaseModel, ctx: ToolContext, result: ToolResult
    ) -> None:
        """Hook called after a tool finishes.

        Mirrors Think's ``afterToolCall`` hook.
        """
        if result.success:
            result_size = len(json.dumps(result.output, ensure_ascii=False, default=str)) if result.output is not None else 0
            logger.info(
                "Tool %s succeeded (%d bytes, %d ms)",
                tool.name,
                result_size,
                result.duration_ms,
            )
        else:
            logger.error(
                "Tool %s failed (%d ms): %s",
                tool.name,
                result.duration_ms,
                result.error,
            )

        await self.broadcast({
            "type": "tool_finish",
            "tool": tool.name,
            "success": result.success,
            "duration_ms": result.duration_ms,
            "error": result.error[:200] if result.error else None,
        })

    # ── Chat / follow-up (Phase 3) ──

    async def on_chat_message(self, message: str) -> None:
        """Handle user follow-up messages.

        Think-aligned turn lifecycle:
          1. Classify intent
          2. before_turn(ctx)  →  optional TurnConfig overrides
          3. Dispatch to handler
          4. after_turn(ctx)
          5. Persist workspace
        """
        # Append user message to conversation history
        self._add_to_conversation("user", message)
        await self.broadcast({"type": "chat_message", "role": "user", "content": message})

        intent = self._classify_intent(message)
        await self.broadcast({"type": "decision", "stage": "intent_classified", "payload": {"intent": intent}})

        # Assemble turn context
        turn_ctx = TurnContext(
            user_message=message,
            intent=intent,
            tools_available=list(self._tools.keys()),
            continuation=False,
        )

        # before_turn hook
        turn_config = await self.before_turn(turn_ctx)
        active_tools = self._tools
        if turn_config is not None and turn_config.active_tools is not None:
            active_tools = {k: v for k, v in self._tools.items() if k in turn_config.active_tools}

        try:
            if intent == "deepen":
                await self._handle_deepen(message, active_tools)
            elif intent == "compare":
                await self._handle_compare(message)
            elif intent == "share_card":
                await self._handle_share_card(message, active_tools)
            else:
                await self._handle_question(message)
        finally:
            # after_turn hook (always fires)
            await self.after_turn(turn_ctx)
            await self._persist_workspace()

    def _add_to_conversation(self, role: str, content: str) -> None:
        """Add a message to the conversation history (kept in workspace)."""
        if "conversation" not in self.workspace:
            self.workspace["conversation"] = []
        self.workspace["conversation"].append({
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        # Keep only the last N messages to prevent workspace bloat
        self.workspace["conversation"] = self.workspace["conversation"][-self.session.max_conversation_messages:]

    def _classify_intent(self, message: str) -> str:
        """Lightweight rule-based intent classification."""
        m = message.lower()
        deepen_keywords = ["深化", "深入", "补充", "更多", "不够", "不足", "再分析", "详细", "展开"]
        compare_keywords = ["对比", "比较", "vs", "versus", "区别", "差异", "哪个"]
        share_card_keywords = ["卡片", "分享", "朋友圈", "海报", "png", "图片"]

        for kw in deepen_keywords:
            if kw in m:
                return "deepen"
        for kw in compare_keywords:
            if kw in m:
                return "compare"
        for kw in share_card_keywords:
            if kw in m:
                return "share_card"
        return "question"

    async def _handle_deepen(self, message: str, active_tools: dict[str, Tool] | None = None) -> None:
        """Deepen analysis on a specific angle."""
        angle_idx = self._extract_angle_index(message)
        reports = self.workspace.get("reports", [])

        if angle_idx is None or angle_idx >= len(reports):
            reply = "请指明你想深化哪个角度的分析（比如\"深化第一个角度\"）。"
            await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
            self._add_to_conversation("assistant", reply)
            return

        report_data = reports[angle_idx]
        report = report_data.get("report", {})
        note_ids = report_data.get("note_ids", [])

        await self.broadcast({
            "type": "progress",
            "message": f"正在深化\"{report.get('title', '角度')}\"的分析...",
        })

        from app.intelligence.insights.tools.write import WriteReportParams

        notes_content, included_count = self._build_notes_content(
            note_ids,
            self.workspace.get("note_map", {}),
        )
        note_index = [
            (nid, self.workspace["note_map"][nid].get("title") or nid)
            for nid in note_ids
            if nid in self.workspace.get("note_map", {})
        ]

        deepen_instruction = f"请对之前的分析进行更深入的探讨。用户反馈：{message}"

        write_params = WriteReportParams(
            angle_name=report.get("title", "深化分析"),
            angle_description=f"{report.get('description', '')}\n\n{deepen_instruction}",
            type_hint=report.get("type", "pattern"),
            notes_content=notes_content,
            note_count=included_count,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            group_index=angle_idx + 1,
            note_index=note_index,
        )

        tools = active_tools or self._tools
        tool = tools.get("write_report")
        if tool is None:
            await self.broadcast({"type": "error", "message": "write_report tool not found"})
            return

        result = await self._execute_tool(tool, write_params)

        if result.success and result.output is not None:
            new_report = result.output
            # Update workspace with the deepened report
            reports[angle_idx] = {
                "report": new_report.model_dump() if hasattr(new_report, "model_dump") else new_report,
                "note_ids": note_ids,
            }
            self.workspace["reports"] = reports

            reply = f"已深化\"{new_report.title}\"的分析：\n\n{new_report.report_markdown[:500]}..."
            await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
            self._add_to_conversation("assistant", reply)
        else:
            err = f"深化分析失败: {result.error}"
            await self.broadcast({"type": "chat_message", "role": "assistant", "content": err})
            self._add_to_conversation("assistant", err)

    async def _handle_compare(self, message: str) -> None:
        """Compare two angles."""
        reports = self.workspace.get("reports", [])
        if len(reports) < 2:
            reply = "当前报告数量不足，无法进行角度对比。"
            await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
            self._add_to_conversation("assistant", reply)
            return

        r1 = reports[0].get("report", {})
        r2 = reports[1].get("report", {})

        comparison = (
            f"**{r1.get('title', '角度A')}** vs **{r2.get('title', '角度B')}**\n\n"
            f"- 角度A 聚焦：{r1.get('description', '')[:100]}...\n"
            f"- 角度B 聚焦：{r2.get('description', '')[:100]}...\n\n"
            f"两者分别从不同维度审视了你的笔记内容。"
        )
        await self.broadcast({"type": "chat_message", "role": "assistant", "content": comparison})
        self._add_to_conversation("assistant", comparison)

    async def _handle_share_card(self, message: str, active_tools: dict[str, Tool] | None = None) -> None:
        """Regenerate share card for a specific angle."""
        angle_idx = self._extract_angle_index(message) or 0
        reports = self.workspace.get("reports", [])

        if angle_idx >= len(reports):
            reply = "请指明你想为哪个报告生成分享卡片。"
            await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
            self._add_to_conversation("assistant", reply)
            return

        report_data = reports[angle_idx]
        report = report_data.get("report", {})

        await self.broadcast({"type": "progress", "message": "正在生成分享卡片..."})

        from app.intelligence.insights.tools.render import RenderShareCardParams

        render_params = RenderShareCardParams(
            report_type=report.get("type", "report"),
            title=report.get("title", ""),
            description=report.get("description", ""),
            confidence=report.get("confidence", 0.7),
            importance_score=report.get("importance_score", 0.7),
            novelty_score=report.get("novelty_score", 0.5),
            evidence_items=[ev.model_dump() if hasattr(ev, "model_dump") else ev for ev in report.get("evidence_items", [])],
            action_items=[act.model_dump() if hasattr(act, "model_dump") else act for act in report.get("action_items", [])],
            raw_share_card=report.get("share_card"),
        )

        tools = active_tools or self._tools
        tool = tools.get("render_share_card")
        if tool:
            result = await self._execute_tool(tool, render_params)
            if result.success:
                reply = f"分享卡片已更新：\"{report.get('title')}\""
                await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
                await self.broadcast({"type": "share_card_ready", "angle_index": angle_idx})
                self._add_to_conversation("assistant", reply)
                return

        err = "分享卡片生成失败"
        await self.broadcast({"type": "chat_message", "role": "assistant", "content": err})
        self._add_to_conversation("assistant", err)

    async def _handle_question(self, message: str) -> None:
        """Answer a question based on existing reports (RAG-style)."""
        reports = self.workspace.get("reports", [])
        if not reports:
            reply = "暂无可用报告，请先完成洞察生成。"
            await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
            self._add_to_conversation("assistant", reply)
            return

        context_parts = []
        for idx, rd in enumerate(reports):
            r = rd.get("report", {})
            context_parts.append(
                f"【报告 {idx + 1}】{r.get('title', '')}\n"
                f"{r.get('description', '')[:200]}..."
            )
        context = "\n\n".join(context_parts)

        reply = (
            f"基于现有 {len(reports)} 篇洞察报告，这里是相关信息：\n\n"
            f"{context[:800]}...\n\n"
            f"如需更具体的回答，请尝试深化某个角度的分析。"
        )
        await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
        self._add_to_conversation("assistant", reply)

    def _extract_angle_index(self, message: str) -> int | None:
        """Extract angle index from user message (1-based → 0-based)."""
        patterns = [
            r"第\s*(\d+)\s*个",
            r"(\d+)\s*号",
            r"角度\s*(\d+)",
            r"report\s*(\d+)",
            r"angle\s*(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, message)
            if m:
                idx = int(m.group(1)) - 1
                return max(0, idx)
        if "第一个" in message or "首个" in message:
            return 0
        if "第二个" in message:
            return 1
        if "第三个" in message:
            return 2
        if "第四个" in message:
            return 3
        if "第五个" in message:
            return 4
        return None

    # ── Tool execution (Think-aligned) ──

    async def _execute_tool(self, tool: Tool, params: BaseModel) -> ToolResult:
        """Execute a tool with Think-style lifecycle hooks.

        Execution flow:
          1. before_tool_call(tool, params, ctx)
          2. Check needs_approval (if approved or not required)
          3. tool.execute(params, ctx)  →  runs handler + before/after hooks
          4. after_tool_call(tool, params, ctx, result)
        """
        ctx = ToolContext(
            db=self.db,
            user_id=self.user_id,
            generation_id=self.generation_id,
            agent=self,
        )

        # 1. Agent-level before hook
        await self.before_tool_call(tool, params, ctx)

        # 2. Check approval ( Think-style needsApproval )
        if tool.check_needs_approval(params):
            await self.broadcast({
                "type": "tool_approval_requested",
                "tool": tool.name,
                "input": params.model_dump() if hasattr(params, "model_dump") else dict(params),
            })
            # In the current FastAPI backend we don't have a real approval
            # flow, so we auto-approve for now. A full implementation would
            # pause here and wait for client response.
            logger.info("Tool %s requires approval — auto-approving in current backend", tool.name)

        # 3. Execute (handler + tool-level before/after hooks)
        try:
            result = await tool.execute(params, ctx)
        except Exception as exc:
            logger.exception("Tool %s failed: %s", tool.name, exc)
            result = ToolResult(
                output=None,
                success=False,
                error=str(exc),
                duration_ms=0,
            )

        # 4. Agent-level after hook
        await self.after_tool_call(tool, params, ctx, result)

        return result

    def _build_notes_content(
        self,
        note_ids: list[str],
        note_map: dict[str, dict],
        max_notes_per_angle: int = 8,
        max_content_chars: int = 15000,
    ) -> tuple[str, int]:
        """Build full note content for a set of note IDs, respecting limits."""
        parts = []
        total_chars = 0
        included = 0

        for nid in note_ids[:max_notes_per_angle]:
            note = note_map.get(nid)
            if not note:
                continue
            content = note["content"]
            tags = ", ".join(note["tags"]) if note.get("tags") else "无标签"

            if total_chars + len(content) > max_content_chars:
                remaining = max_content_chars - total_chars
                if remaining < 200:
                    break
                content = content[:remaining] + "\n...(截断)"

            parts.append(
                f"### {note['title']} (ID: {nid})\n"
                f"标签: {tags} | 创建于: {note.get('created_at', '未知')}\n\n"
                f"{content}\n"
            )
            total_chars += len(content)
            included += 1

        return "\n---\n".join(parts), included

    # ── Event broadcasting ──

    async def broadcast(self, event: dict[str, Any]) -> int:
        """Broadcast an event to the persistent event store."""
        self._sequence = await append_event(self.db, self.generation_id, event)
        return self._sequence

    async def broadcast_progress(self, message: str, group: int | None = None) -> int:
        """Convenience helper for progress messages."""
        event: dict[str, Any] = {"type": "progress", "message": message}
        if group is not None:
            event["group"] = group
        return await self.broadcast(event)

    async def broadcast_decision(self, stage: str, payload: dict[str, Any], group: int | None = None) -> int:
        """Convenience helper for decision events."""
        event: dict[str, Any] = {"type": "decision", "stage": stage, "payload": payload}
        if group is not None:
            event["group"] = group
        return await self.broadcast(event)

    async def broadcast_thinking_delta(self, text: str, group: int) -> int:
        return await self.broadcast({"type": "thinking_delta", "group": group, "text": text})

    async def broadcast_markdown_delta(self, text: str, group: int) -> int:
        return await self.broadcast({"type": "markdown_delta", "group": group, "text": text})

    # ── Workspace persistence ──

    async def _persist_workspace(self) -> None:
        """Save the agent's workspace to the DB.

        Syncs the legacy workspace dict with the session blocks before saving.
        """
        # Sync session memory block back to workspace
        memory_block = self.session.get_block("memory")
        if memory_block is not None:
            self.workspace["memory"] = memory_block.content

        generation = await self.db.get(InsightGeneration, self.generation_id)
        if generation is not None:
            generation.workspace_json = json.dumps(self.workspace, ensure_ascii=False, default=str)
            await self.db.commit()

    async def _restore_workspace(self) -> None:
        """Restore the agent's workspace from the DB.

        Also rehydrates the session from the workspace.
        """
        generation = await self.db.get(InsightGeneration, self.generation_id)
        if generation is not None and generation.workspace_json:
            try:
                self.workspace = json.loads(generation.workspace_json)
            except json.JSONDecodeError:
                self.workspace = {}

        # Rehydrate session blocks from workspace
        self.session = self.configure_session(self.session)
