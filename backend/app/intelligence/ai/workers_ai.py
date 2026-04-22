"""Cloudflare Workers AI provider.

Routes all LLM calls through Cloudflare Workers AI via its OpenAI-compatible
REST endpoint at:
  https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1

Authentication uses a Cloudflare API token (CF_API_TOKEN).  No third-party
subscription keys are required — only your own Cloudflare account credentials.

Reasoning models (e.g. @cf/deepseek-ai/deepseek-r1-distill-qwen-32b) emit
chain-of-thought inside <think>…</think> tags in the content stream; the
higher-level streaming layer (_ThinkBlockSplitter) already handles this.
"""
from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.intelligence.ai.provider import AIProvider, AIResponseFormat
from app.config import settings

logger = logging.getLogger(__name__)


class WorkersAIProvider(AIProvider):
    """Cloudflare Workers AI implementation of AIProvider."""

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        profile: str = "default",
        response_format: AIResponseFormat | None = None,
    ) -> str:
        client = AsyncOpenAI(
            api_key=settings.CF_API_TOKEN,
            base_url=settings.cf_ai_base_url,
        )

        kwargs: dict = {
            "model": settings.AI_MODEL,
            "max_tokens": settings.AI_MAX_TOKENS,
            "temperature": settings.AI_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format is not None:
            kwargs["response_format"] = response_format.to_openai_payload()

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice and choice.message else ""
        if isinstance(content, list):
            parts: list[str] = [
                item.get("text", "") for item in content if isinstance(item, dict)
            ]
            content = "".join(parts)
        if not isinstance(content, str):
            raise TypeError("AI response content must be a string")

        logger.info("WorkersAI response received (%d chars)", len(content))
        return content
