from __future__ import annotations

from copy import deepcopy

INSIGHT_WORKFLOW_VERSION = "single-agent-v1"

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


def build_insight_task_config() -> dict[str, object]:
    return deepcopy(_INSIGHT_TASK_PROFILE)
