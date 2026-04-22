from __future__ import annotations

from app.intelligence.ai.provider import AIProvider
from app.config import settings


def get_provider() -> AIProvider:
    """Factory: return the configured AI provider (cloudflare | openrouter)."""
    if settings.AI_PROVIDER == "openrouter":
        from app.intelligence.ai.archive.openrouter_legacy import OpenRouterProvider
        return OpenRouterProvider()
    from app.intelligence.ai.workers_ai import WorkersAIProvider
    return WorkersAIProvider()
