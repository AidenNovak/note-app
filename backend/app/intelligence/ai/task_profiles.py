from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class AITaskProfile:
    name: str
    allowed_tools: tuple[str, ...] = ()
    max_turns: int = 1
    effort: str = "low"


def get_task_profile(name: str) -> AITaskProfile:
    profiles = {
        "default": AITaskProfile(name="default"),
        "note_metadata": AITaskProfile(name="note_metadata"),
        "note_rewrite": AITaskProfile(name="note_rewrite", max_turns=2),
        "tag_extraction": AITaskProfile(name="tag_extraction"),
        "mind_synthesis": AITaskProfile(name="mind_synthesis"),
        "generic": AITaskProfile(
            name="generic",
            allowed_tools=("Grep", "Read", "Skill", "WebSearch", "WebFetch"),
            max_turns=8,
        ),
        "insight": AITaskProfile(
            name="insight",
            allowed_tools=("Grep", "Read", "Skill", "WebSearch", "WebFetch"),
            max_turns=max(settings.INSIGHT_AGENT_MAX_TURNS, 5),
        ),
    }
    return profiles.get(name, profiles["default"])
