"""InsightAgent — lightweight stateful agent for insight generation.

Inspired by Cloudflare Agents. The agent owns its workspace and event stream.
It survives disconnections and can be loaded from the database for follow-up
conversations.

Unlike the previous version, this agent does NOT use a state machine or
step-based execution. The generation pipeline is a straightforward async
function (see ``pipeline.py``). The agent's role is:

  1. Broadcast events during generation
  2. Persist workspace for crash recovery and conversation history
  3. Handle follow-up chat messages after generation completes
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.intelligence.insights.event_store import (
    append_event,
    clear_buffers,
    flush_events,
)
from app.intelligence.insights.tools import ALL_TOOLS
from app.intelligence.insights.tools.base import Tool, ToolContext, ToolResult
from app.models import InsightGeneration, TaskStatus

logger = logging.getLogger(__name__)


class InsightAgent:
    """Lightweight insight agent.

    Usage (generation):
        agent = InsightAgent(generation_id, user_id, db)
        await agent.on_start()
        # ... pipeline runs directly in pipeline.py ...
        await agent.on_finish(status, summary)

    Usage (chat after generation):
        agent = await InsightAgent.load(generation_id, db)
        await agent.on_chat_message(message)
    """

    def __init__(
        self,
        generation_id: str,
        user_id: str,
        db: AsyncSession,
    ):
        self.generation_id = generation_id
        self.user_id = user_id
        self.db = db
        self.workspace: dict[str, Any] = {}
        self.tools: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}
        self._sequence = 0

    @classmethod
    async def load(cls, generation_id: str, db: AsyncSession) -> "InsightAgent":
        """Restore an agent from the database (workspace only)."""
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

    # ── Lifecycle ──

    async def on_start(self) -> None:
        """Called when generation begins."""
        await self._restore_workspace()
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

    # ── Chat / follow-up (Phase 3) ──

    async def on_chat_message(self, message: str) -> None:
        """Handle user follow-up messages.

        Classifies intent and dispatches to the appropriate handler.
        All handlers broadcast events so the client can stream responses.
        """
        # Append user message to conversation history
        self._add_to_conversation("user", message)
        await self.broadcast({"type": "chat_message", "role": "user", "content": message})

        intent = self._classify_intent(message)
        await self.broadcast({"type": "decision", "stage": "intent_classified", "payload": {"intent": intent}})

        if intent == "deepen":
            await self._handle_deepen(message)
        elif intent == "compare":
            await self._handle_compare(message)
        elif intent == "share_card":
            await self._handle_share_card(message)
        else:
            await self._handle_question(message)

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
        # Keep only the last 40 messages to prevent workspace bloat
        self.workspace["conversation"] = self.workspace["conversation"][-40:]

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

    async def _handle_deepen(self, message: str) -> None:
        """Deepen analysis on a specific angle."""
        angle_idx = self._extract_angle_index(message)
        reports = self.workspace.get("reports", [])

        if angle_idx is None or angle_idx >= len(reports):
            await self.broadcast({
                "type": "chat_message",
                "role": "assistant",
                "content": "请指明你想深化哪个角度的分析（比如\"深化第一个角度\"）。",
            })
            self._add_to_conversation("assistant", "请指明你想深化哪个角度的分析（比如\"深化第一个角度\"）。")
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

        tool = self.tools.get("write_report")
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
            await self.broadcast({
                "type": "chat_message",
                "role": "assistant",
                "content": "当前报告数量不足，无法进行角度对比。",
            })
            self._add_to_conversation("assistant", "当前报告数量不足，无法进行角度对比。")
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

    async def _handle_share_card(self, message: str) -> None:
        """Regenerate share card for a specific angle."""
        angle_idx = self._extract_angle_index(message) or 0
        reports = self.workspace.get("reports", [])

        if angle_idx >= len(reports):
            await self.broadcast({
                "type": "chat_message",
                "role": "assistant",
                "content": "请指明你想为哪个报告生成分享卡片。",
            })
            self._add_to_conversation("assistant", "请指明你想为哪个报告生成分享卡片。")
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

        tool = self.tools.get("render_share_card")
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
            await self.broadcast({
                "type": "chat_message",
                "role": "assistant",
                "content": "暂无可用报告，请先完成洞察生成。",
            })
            self._add_to_conversation("assistant", "暂无可用报告，请先完成洞察生成。")
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

    # ── Tool execution ──

    async def _execute_tool(self, tool: Tool, params: Any) -> ToolResult:
        """Execute a tool with built-in broadcast events."""
        await self.broadcast({
            "type": "tool_start",
            "tool": tool.name,
        })

        ctx = ToolContext(
            db=self.db,
            user_id=self.user_id,
            generation_id=self.generation_id,
            agent=self,
        )

        import time
        started = time.perf_counter()
        try:
            result = await tool.handler(params, ctx)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = ToolResult(
                output=None,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )
            logger.exception("Tool %s failed: %s", tool.name, exc)

        await self.broadcast({
            "type": "tool_finish",
            "tool": tool.name,
            "success": result.success,
            "duration_ms": result.duration_ms,
            "error": result.error[:200] if result.error else None,
        })

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
        """Save the agent's workspace to the DB."""
        generation = await self.db.get(InsightGeneration, self.generation_id)
        if generation is not None:
            generation.workspace_json = json.dumps(self.workspace, ensure_ascii=False, default=str)
            await self.db.commit()

    async def _restore_workspace(self) -> None:
        """Restore the agent's workspace from the DB."""
        generation = await self.db.get(InsightGeneration, self.generation_id)
        if generation is not None and generation.workspace_json:
            try:
                self.workspace = json.loads(generation.workspace_json)
            except json.JSONDecodeError:
                self.workspace = {}
