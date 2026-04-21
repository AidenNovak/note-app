from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from abc import ABC, abstractmethod


@dataclass(frozen=True)
class AIResponseFormat:
    """Provider-agnostic structured output request."""

    type: Literal["json_object", "json_schema"]
    name: str | None = None
    schema: dict[str, Any] | None = None
    strict: bool = True

    def to_openai_payload(self) -> dict[str, Any]:
        if self.type == "json_object":
            return {"type": "json_object"}
        if not self.name or self.schema is None:
            raise ValueError("json_schema response_format requires both name and schema")
        return {
            "type": "json_schema",
            "json_schema": {
                "name": self.name,
                "strict": self.strict,
                "schema": self.schema,
            },
        }


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        profile: str = "default",
        response_format: AIResponseFormat | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant message."""
        ...
