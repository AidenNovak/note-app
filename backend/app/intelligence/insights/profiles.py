from __future__ import annotations

from copy import deepcopy

INSIGHT_WORKFLOW_VERSION = "single-agent-v1"
INSIGHT_AI_SDK_VERSION = "ai-sdk-v1"

_INSIGHT_TASK_PROFILE: dict[str, object] = {
    "type": "insight",
    "workflow_version": INSIGHT_WORKFLOW_VERSION,
    "workflow_mode": "single",
    "stages": [
        {
            "key": "insight",
            "agent": "insight-analyst",
            "kind": "insight",
            "allowed_tools": ["Grep", "Read"],
            "effort": "high",
        },
    ],
}

# AI SDK workflow profile (uses Vercel AI SDK instead of Claude Agent SDK)
_AI_SDK_TASK_PROFILE: dict[str, object] = {
    "type": "insight",
    "workflow_version": INSIGHT_AI_SDK_VERSION,
    "workflow_mode": "single",
    "script": "ai_sdk_insight_agent.mjs",
    "stages": [
        {
            "key": "insight",
            "agent": "insight-analyst",
            "kind": "insight",
            "effort": "high",
        },
    ],
}


def build_insight_task_config(use_ai_sdk: bool = False) -> dict[str, object]:
    """Build insight task configuration.
    
    Args:
        use_ai_sdk: If True, use Vercel AI SDK workflow instead of Claude Agent SDK
    """
    if use_ai_sdk:
        return deepcopy(_AI_SDK_TASK_PROFILE)
    return deepcopy(_INSIGHT_TASK_PROFILE)
