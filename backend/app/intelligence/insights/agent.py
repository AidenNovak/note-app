from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from app.config import settings

APP_ROOT = Path(__file__).resolve().parents[4]
CLAUDE_HELPER_SCRIPT = APP_ROOT / "scripts" / "claude_insight_agent.mjs"
AI_SDK_HELPER_SCRIPT = APP_ROOT / "scripts" / "ai_sdk_insight_agent.mjs"


from typing import AsyncGenerator


async def run_claude_insight_agent_stream(
    workspace_path: Path,
) -> AsyncGenerator[dict[str, object], None]:
    """Run the legacy Claude Agent SDK insight generator."""
    if not CLAUDE_HELPER_SCRIPT.exists():
        raise RuntimeError(f"Missing Claude insight helper script at {CLAUDE_HELPER_SCRIPT}")

    env = os.environ.copy()
    env.setdefault("CLAUDE_AGENT_SDK_ROOT", settings.CLAUDE_AGENT_SDK_ROOT)
    env.setdefault("INSIGHT_AGENT_MAX_TURNS", str(settings.INSIGHT_AGENT_MAX_TURNS))

    process = await asyncio.create_subprocess_exec(
        "node",
        str(CLAUDE_HELPER_SCRIPT),
        str(workspace_path),
        cwd=str(APP_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    final_payload = None
    stderr_lines = []

    async def _read_stderr():
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            stderr_lines.append(line.decode().strip())

    stderr_task = asyncio.create_task(_read_stderr())

    while True:
        line = await process.stdout.readline()
        if not line:
            break
        decoded = line.decode().strip()
        if decoded.startswith("PROGRESS:"):
            try:
                event_json = json.loads(decoded[len("PROGRESS:") :].strip())
                yield {"type": "progress", "data": event_json}
            except json.JSONDecodeError:
                pass
        elif decoded.startswith("{"):
            try:
                final_payload = json.loads(decoded)
            except json.JSONDecodeError:
                pass

    await process.wait()
    await stderr_task

    if process.returncode != 0:
        error_msg = "\n".join(stderr_lines) or "Claude insight agent failed"
        raise RuntimeError(error_msg)

    if not final_payload:
        raise RuntimeError("Claude insight agent did not return a final JSON payload")

    reports = final_payload.get("reports")
    if not isinstance(reports, list):
        raise RuntimeError("Claude insight agent did not return a reports list")

    yield {"type": "final", "data": final_payload}


async def run_ai_sdk_insight_agent_stream(
    workspace_path: Path,
) -> AsyncGenerator[dict[str, object], None]:
    """Run the Vercel AI SDK insight generator.
    
    Requires AI_SDK_PROVIDER and AI_SDK_API_KEY environment variables.
    Supports: openai, anthropic, google, openrouter
    """
    if not AI_SDK_HELPER_SCRIPT.exists():
        raise RuntimeError(f"Missing AI SDK helper script at {AI_SDK_HELPER_SCRIPT}")

    env = os.environ.copy()
    
    # AI SDK configuration from settings or environment
    env.setdefault("AI_SDK_PROVIDER", settings.AI_PROVIDER)
    env.setdefault("AI_SDK_MODEL", getattr(settings, "AI_SDK_MODEL", "gpt-4o"))
    env.setdefault("AI_SDK_API_KEY", settings.OPENROUTER_API_KEY or "")
    env.setdefault("AI_SDK_BASE_URL", settings.OPENROUTER_BASE_URL)
    env.setdefault("AI_SDK_MAX_TOKENS", str(settings.AI_MAX_TOKENS))
    env.setdefault("AI_SDK_TEMPERATURE", str(settings.AI_TEMPERATURE))
    env.setdefault("AI_SDK_STREAMING", "true")

    process = await asyncio.create_subprocess_exec(
        "node",
        str(AI_SDK_HELPER_SCRIPT),
        str(workspace_path),
        cwd=str(APP_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    final_payload = None
    stderr_lines = []

    async def _read_stderr():
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            stderr_lines.append(line.decode().strip())

    stderr_task = asyncio.create_task(_read_stderr())

    while True:
        line = await process.stdout.readline()
        if not line:
            break
        decoded = line.decode().strip()
        if decoded.startswith("PROGRESS:"):
            try:
                event_json = json.loads(decoded[len("PROGRESS:") :].strip())
                yield {"type": "progress", "data": event_json}
            except json.JSONDecodeError:
                pass
        elif decoded.startswith("{"):
            try:
                final_payload = json.loads(decoded)
            except json.JSONDecodeError:
                pass

    await process.wait()
    await stderr_task

    if process.returncode != 0:
        error_msg = "\n".join(stderr_lines) or "AI SDK insight agent failed"
        raise RuntimeError(error_msg)

    if not final_payload:
        raise RuntimeError("AI SDK insight agent did not return a final JSON payload")

    reports = final_payload.get("reports")
    if not isinstance(reports, list):
        raise RuntimeError("AI SDK insight agent did not return a reports list")

    yield {"type": "final", "data": final_payload}
