from __future__ import annotations

import logging

import httpx

from app.intelligence.ai.provider import AIProvider
from app.config import settings

logger = logging.getLogger(__name__)

API_URL = f"{settings.OPENROUTER_BASE_URL}/chat/completions"


class OpenRouterProvider(AIProvider):
    """OpenRouter API implementation."""

    async def generate(self, system_prompt: str, user_prompt: str, *, profile: str = "default") -> str:
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.OPENROUTER_MODEL,
            "max_tokens": settings.AI_MAX_TOKENS,
            "temperature": settings.AI_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            logger.info("OpenRouter response received (%d chars)", len(content))
            return content
