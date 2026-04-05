from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from app.intelligence.insights.agent import run_claude_insight_agent_stream


class AgentEngine:
    def __init__(self):
        self.backend_root = Path(__file__).resolve().parents[2]

    async def run_task(
        self,
        *,
        task_name: str,
        task_config: dict[str, Any],
        workspace_path: Path | None = None,
        context_data: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        if workspace_path is None:
            workspace_id = f"task_{task_name}_{uuid.uuid4().hex[:8]}"
            workspace_path = self.backend_root / "data" / "tasks" / workspace_id
        workspace_path.mkdir(parents=True, exist_ok=True)

        (workspace_path / "task_config.json").write_text(
            json.dumps(task_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if context_data is not None:
            (workspace_path / "context.json").write_text(
                json.dumps(context_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        async for event in run_claude_insight_agent_stream(workspace_path):
            yield event


agent_engine = AgentEngine()
