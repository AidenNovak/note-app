"""InsightAgent — stateful agent for insight generation.

Inspired by Cloudflare's ``Think`` base class. The agent owns its lifecycle,
workspace, and event stream. It survives disconnections and can be resumed
from the database.

State machine:
    IDLE → DISCOVERING → GENERATING → REVIEWING → IDLE
      ↑_________________________________________|
    (any state can transition to FAILED)

Execution modes:
    pipeline — hardcoded 3-phase flow (default, behaviour-identical to v1)
    auto     — LLM-driven agentic loop (future)
"""
from __future__ import annotations

import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.intelligence.insights.event_store import (
    append_event,
    clear_buffers,
    flush_events,
    get_events,
    get_latest_sequence,
)
from app.intelligence.insights.tools import ALL_TOOLS
from app.intelligence.insights.tools.base import Tool, ToolContext, ToolResult
from app.models import InsightGeneration, TaskStatus

logger = logging.getLogger(__name__)


class AgentState(str, enum.Enum):
    IDLE = "idle"
    DISCOVERING = "discovering"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    AWAITING_HUMAN = "awaiting_human"
    FAILED = "failed"


class ExecutionMode(str, enum.Enum):
    PIPELINE = "pipeline"
    AUTO = "auto"
    INTERACTIVE = "interactive"


@dataclass
class TurnResult:
    """Result of a single agent turn."""

    tool_name: str = ""
    output: Any = None
    result: ToolResult | None = None


class InsightAgent:
    """Stateful insight generation agent.

    Usage (pipeline mode — default):
        agent = InsightAgent(generation_id, user_id, db)
        await agent.on_start()
        await agent.run_pipeline(clusters, angles, note_map, ...)
        await agent.on_finish()

    Usage (auto mode — future):
        agent = InsightAgent(...)
        await agent.on_start()
        while agent.state != AgentState.REVIEWING:
            await agent.run_turn()
        await agent.on_finish()
    """

    def __init__(
        self,
        generation_id: str,
        user_id: str,
        db: AsyncSession,
        mode: ExecutionMode = ExecutionMode.PIPELINE,
    ):
        self.generation_id = generation_id
        self.user_id = user_id
        self.db = db
        self.mode = mode
        self.state = AgentState.IDLE
        self.workspace: dict[str, Any] = {}
        self.tools: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}
        self._sequence = 0

    @classmethod
    async def load(cls, generation_id: str, db: AsyncSession) -> "InsightAgent":
        """Restore an agent from the database (workspace + state)."""
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

    # ── Lifecycle hooks ──

    async def on_start(self) -> None:
        """Called when the agent begins work. Restores workspace from DB if resuming."""
        await self._restore_workspace()
        await self.transition_to(AgentState.DISCOVERING)
        await self.broadcast({
            "type": "starting",
            "message": "Insight Agent 启动...",
        })

    async def on_finish(self, status: TaskStatus = TaskStatus.COMPLETED, summary: str = "") -> None:
        """Called when the agent completes or fails."""
        if status == TaskStatus.COMPLETED:
            await self.transition_to(AgentState.REVIEWING)
            await self.broadcast({
                "type": "completed",
                "summary": summary or "洞察分析完成",
            })
        else:
            await self.transition_to(AgentState.FAILED)
            await self.broadcast({
                "type": "error",
                "message": summary or "洞察分析失败",
            })
        await self._persist_workspace()
        await flush_events(self.db, self.generation_id)
        clear_buffers(self.generation_id)

    async def before_turn(self) -> None:
        """Hook called before each agent turn (override in subclass)."""

    async def on_step_finish(self, turn_result: TurnResult) -> None:
        """Hook called after each tool execution (override in subclass)."""

    async def on_chat_message(self, message: str) -> None:
        """Handle user follow-up messages (Phase 3).

        Classifies intent and dispatches to the appropriate handler.
        All handlers broadcast events so the client can stream responses.
        """
        if self.state != AgentState.REVIEWING:
            await self.broadcast({
                "type": "error",
                "message": "Agent 尚未就绪，请等待报告生成完成后再交互。",
            })
            return

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
        self.workspace["conversation"].append({"role": role, "content": content, "ts": datetime.now(timezone.utc).isoformat()})
        # Keep only the last 40 messages to prevent workspace bloat
        self.workspace["conversation"] = self.workspace["conversation"][-40:]

    def _classify_intent(self, message: str) -> str:
        """Lightweight rule-based intent classification.

        Future: replace with a small LLM call for richer intent understanding.
        """
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
        # Try to identify which angle the user is referring to
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

        # Re-run write_report tool with a deepen instruction
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

        # Simple: compare first two angles
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

        # Build a simple context from all reports
        context_parts = []
        for idx, rd in enumerate(reports):
            r = rd.get("report", {})
            context_parts.append(
                f"【报告 {idx + 1}】{r.get('title', '')}\n"
                f"{r.get('description', '')[:200]}..."
            )
        context = "\n\n".join(context_parts)

        # Simple heuristic answer (future: call LLM with context)
        reply = (
            f"基于现有 {len(reports)} 篇洞察报告，这里是相关信息：\n\n"
            f"{context[:800]}...\n\n"
            f"如需更具体的回答，请尝试深化某个角度的分析。"
        )
        await self.broadcast({"type": "chat_message", "role": "assistant", "content": reply})
        self._add_to_conversation("assistant", reply)

    def _extract_angle_index(self, message: str) -> int | None:
        """Extract angle index from user message (1-based → 0-based)."""
        import re
        # Match patterns like "第一个", "第1个", "1号", "角度1"
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
        # Check for ordinal words
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

    # ── State transitions ──

    async def transition_to(self, new_state: AgentState) -> None:
        """Transition the agent to a new state, persisting state + workspace to DB."""
        old_state = self.state
        self.state = new_state

        generation = await self.db.get(InsightGeneration, self.generation_id)
        if generation is not None:
            generation.session_state = new_state.value
            generation.workspace_json = json.dumps(self.workspace, ensure_ascii=False, default=str)
            await self.db.commit()

        logger.info(
            "Agent %s: %s → %s",
            self.generation_id,
            old_state.value,
            new_state.value,
        )

    # ── Tool execution ──

    async def _execute_tool(self, tool: Tool, params: Any) -> ToolResult:
        """Execute a tool with built-in broadcast events."""
        await self.broadcast({
            "type": "tool_start",
            "tool": tool.name,
            "stage": self.state.value,
        })

        ctx = ToolContext(
            db=self.db,
            user_id=self.user_id,
            generation_id=self.generation_id,
            agent=self,
        )

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

    # ── Pipeline mode (default, behaviour-identical to clustered-v1) ──

    async def run_pipeline(self, today: str) -> list[tuple[Any, list[str]]]:
        """Run the hardcoded 3-phase pipeline using Tool interfaces.

        Reads clusters/angles/note_map from workspace (must be prepared by caller
        before invocation). Internally delegates to ``step()`` so the agent can
        resume from any step after a process restart.
        """
        self.workspace["today"] = today
        await self._persist_workspace()

        # Phase 1 — prepare (IDLE → DISCOVERING → GENERATING)
        if self.state == AgentState.IDLE:
            await self.step()
        if self.state == AgentState.DISCOVERING:
            await self.step()

        # Phase 2 — generate reports one at a time
        while self.state == AgentState.GENERATING:
            await self.step()

        # Phase 3 — finalize is handled by caller via on_finish()

        # Collect reports from workspace
        reports_data = self.workspace.get("reports", [])
        reports: list[tuple[Any, list[str]]] = []
        for item in reports_data:
            report_data = item.get("report", {})
            note_ids = item.get("note_ids", [])
            reports.append((report_data, note_ids))

        return reports

    # ── Discrete step execution (resumable) ──

    async def step(self) -> TurnResult:
        """Execute one discrete step.

        The agent determines what to do based on its current state
        and workspace contents. After each step, workspace is checkpointed.
        """
        await self.before_turn()

        if self.state == AgentState.IDLE:
            return await self._step_start()

        if self.state == AgentState.DISCOVERING:
            return await self._step_prepare()

        if self.state == AgentState.AWAITING_HUMAN:
            # Paused for human approval — step is a no-op until resume()
            return TurnResult(tool_name="awaiting_human")

        if self.state == AgentState.GENERATING:
            result = await self._step_write_next_report()
            await self.on_step_finish(result)
            await self._persist_workspace()
            return result

        if self.state == AgentState.REVIEWING:
            return await self._step_finalize()

        logger.warning("Agent %s in unexpected state %s", self.generation_id, self.state)
        return TurnResult()

    async def resume(self, action: str = "approve") -> TurnResult:
        """Resume execution after human-in-the-loop pause.

        Actions: "approve" | "retry" | "skip"
        """
        if self.state != AgentState.AWAITING_HUMAN:
            logger.warning("Resume called but agent not awaiting human: %s", self.state)
            return TurnResult()

        await self.broadcast({
            "type": "human_action",
            "action": action,
            "stage": "angles_approved" if action == "approve" else f"angles_{action}",
        })

        if action == "skip":
            await self.transition_to(AgentState.REVIEWING)
            return TurnResult(tool_name="skip")

        # approve or retry — both continue to GENERATING
        await self.transition_to(AgentState.GENERATING)
        return TurnResult(tool_name="resume")

    async def _step_start(self) -> TurnResult:
        """Step 0: restore workspace and transition to DISCOVERING."""
        await self._restore_workspace()
        await self.transition_to(AgentState.DISCOVERING)
        await self.broadcast({
            "type": "starting",
            "message": "Insight Agent 启动...",
        })
        return TurnResult(tool_name="start")

    async def _step_prepare(self) -> TurnResult:
        """Step 1: validate workspace, broadcast angles, transition to GENERATING (or AWAITING_HUMAN)."""
        clusters = self.workspace.get("clusters", [])
        angles_data = self.workspace.get("angles", [])
        note_map = self.workspace.get("note_map", {})

        if not clusters or not angles_data or not note_map:
            raise ValueError("Workspace not prepared: clusters/angles/note_map missing")

        # Normalize angles
        angles = self._normalize_angles(angles_data)
        self.workspace["angles"] = [a.model_dump() if hasattr(a, "model_dump") else a for a in angles]

        await self.broadcast({
            "type": "clustering",
            "cluster_count": len(clusters),
            "note_count": len(note_map),
            "message": f"发现 {len(clusters)} 个主题簇，共 {len(note_map)} 条笔记",
        })
        await self.broadcast({
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

        # Reset progress counter
        self.workspace["_current_angle_index"] = 0

        if self.mode == ExecutionMode.INTERACTIVE:
            await self.transition_to(AgentState.AWAITING_HUMAN)
            await self.broadcast({
                "type": "human_in_the_loop",
                "action_required": "approve_angles",
                "payload": {
                    "angles": [
                        {"name": a.angle_name, "description": a.description, "type_hint": a.type_hint}
                        for a in angles
                    ],
                },
            })
            return TurnResult(tool_name="prepare", output="awaiting_human")

        await self.transition_to(AgentState.GENERATING)
        return TurnResult(tool_name="prepare")

    async def _step_write_next_report(self) -> TurnResult:
        """Step 2: generate the next report (resumable via _current_angle_index)."""
        from app.intelligence.insights.tools.write import WriteReportParams

        angles_data = self.workspace.get("angles", [])
        note_map = self.workspace.get("note_map", {})
        today = self.workspace.get("today", "")
        current_idx = self.workspace.get("_current_angle_index", 0)

        angles = self._normalize_angles(angles_data)
        total_groups = len(angles)

        if current_idx >= total_groups:
            # All reports done
            await self.transition_to(AgentState.REVIEWING)
            return TurnResult(tool_name="write_report")

        angle = angles[current_idx]
        group_index = current_idx + 1

        notes_content, included_count = self._build_notes_content(angle.note_ids, note_map)
        if included_count == 0:
            logger.warning("No notes available for angle '%s'", angle.angle_name)
            self.workspace["_current_angle_index"] = current_idx + 1
            return TurnResult(tool_name="write_report", output=None)

        note_index = [
            (nid, note_map[nid].get("title") or nid)
            for nid in angle.note_ids
            if nid in note_map
        ]

        await self.broadcast({
            "type": "group_started",
            "group": group_index,
            "total_groups": total_groups,
            "theme": angle.angle_name,
            "angle": angle.description,
            "note_count": len(angle.note_ids),
        })

        write_params = WriteReportParams(
            angle_name=angle.angle_name,
            angle_description=angle.description,
            type_hint=angle.type_hint,
            notes_content=notes_content,
            note_count=included_count,
            date=today,
            group_index=group_index,
            note_index=note_index,
        )

        tool = self.tools["write_report"]
        result = await self._execute_tool(tool, write_params)

        if not result.success or result.output is None:
            logger.warning("Report generation failed for angle '%s': %s", angle.angle_name, result.error)
            await self.broadcast({
                "type": "group_completed",
                "group": group_index,
                "total_groups": total_groups,
                "theme": angle.angle_name,
                "title": "",
                "description": f"生成失败: {result.error}",
            })
            self.workspace["_current_angle_index"] = current_idx + 1
            return TurnResult(tool_name="write_report", result=result)

        report = result.output

        # Save report to workspace
        reports = self.workspace.get("reports", [])
        reports.append({
            "report": report.model_dump() if hasattr(report, "model_dump") else report,
            "note_ids": angle.note_ids,
        })
        self.workspace["reports"] = reports
        self.workspace["_current_angle_index"] = current_idx + 1

        await self.broadcast({
            "type": "group_completed",
            "group": group_index,
            "total_groups": total_groups,
            "theme": angle.angle_name,
            "title": report.title,
            "description": report.description,
            "thinking_trace": report.thinking_trace or "",
            "report_markdown": report.report_markdown,
        })

        return TurnResult(tool_name="write_report", output=report, result=result)

    async def _step_finalize(self) -> TurnResult:
        """Step 3: finalize and persist completed reports."""
        await self.broadcast({
            "type": "completed",
            "summary": f"生成完成，共 {len(self.workspace.get('reports', []))} 篇报告",
        })
        return TurnResult(tool_name="finalize")

    def _normalize_angles(self, angles_data: list[Any]) -> list[Any]:
        """Normalize angles from workspace (dicts from JSON or AngleOutput objects)."""
        angles: list[Any] = []
        for a in angles_data:
            if hasattr(a, "angle_name"):
                angles.append(a)
            elif isinstance(a, dict):
                from app.intelligence.insights.schemas_ai import AngleOutput
                angles.append(AngleOutput(**a))
            else:
                angles.append(a)
        return angles

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

    # ── Agentic loop (future: auto mode) ──

    async def run_turn(self) -> TurnResult:
        """Execute one agentic turn.

        In pipeline mode this is a no-op placeholder.
        In auto mode (future) this will run the planning LLM and tool loop.
        """
        if self.mode == ExecutionMode.PIPELINE:
            return TurnResult()

        # Auto mode placeholder
        await self.before_turn()
        # TODO: LLM planner decides next tool
        result = TurnResult()
        await self.on_step_finish(result)
        return result

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
            if generation.session_state:
                try:
                    self.state = AgentState(generation.session_state)
                except ValueError:
                    self.state = AgentState.IDLE

    # ── Streaming helpers for consumers ──

    @staticmethod
    async def get_event_stream(
        db: AsyncSession,
        generation_id: str,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        """Retrieve events for SSE replay (static helper)."""
        events = await get_events(db, generation_id, after_sequence=after_sequence)
        result = []
        for ev in events:
            try:
                payload = json.loads(ev.payload_json)
            except json.JSONDecodeError:
                payload = {"type": ev.event_type, "payload_json": ev.payload_json}
            result.append(payload)
        return result

    @staticmethod
    async def get_latest_sequence(db: AsyncSession, generation_id: str) -> int:
        return await get_latest_sequence(db, generation_id)
