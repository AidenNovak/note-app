from __future__ import annotations

import logging

import httpx

from app.intelligence.ai.provider import AIProvider, AIResponseFormat
from app.config import settings

logger = logging.getLogger(__name__)

API_URL = f"{settings.OPENROUTER_BASE_URL}/chat/completions"


class OpenRouterProvider(AIProvider):
    """OpenRouter API implementation."""

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        profile: str = "default",
        response_format: AIResponseFormat | None = None,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.AI_MODEL,
            "max_tokens": settings.AI_MAX_TOKENS,
            "temperature": settings.AI_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format.to_openai_payload()

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                content = "".join(parts)
            if not isinstance(content, str):
                raise TypeError("AI response content must be a string")
            logger.info("OpenRouter response received (%d chars)", len(content))
            return content
