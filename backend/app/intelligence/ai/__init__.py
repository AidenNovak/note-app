from __future__ import annotations

from app.intelligence.ai.provider import AIProvider


def get_provider() -> AIProvider:
    """Factory: return the configured AI provider."""
    from app.intelligence.ai.openrouter import OpenRouterProvider
    return OpenRouterProvider()
