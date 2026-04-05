from __future__ import annotations

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str, *, profile: str = "default") -> str:
        """Send a chat completion request and return the assistant message."""
        ...
