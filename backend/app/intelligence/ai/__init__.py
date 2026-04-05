from __future__ import annotations

from app.intelligence.ai.provider import AIProvider


def get_provider() -> AIProvider:
    """Factory: return the configured AI provider."""
    from app.config import settings

    if settings.AI_PROVIDER == "openrouter":
        from app.intelligence.ai.openrouter import OpenRouterProvider
        return OpenRouterProvider()
    elif settings.AI_PROVIDER == "claude-sdk":
        from app.intelligence.ai.claude_sdk import ClaudeSDKProvider
        return ClaudeSDKProvider()
    else:
        # Default to Claude SDK
        from app.intelligence.ai.claude_sdk import ClaudeSDKProvider
        return ClaudeSDKProvider()
