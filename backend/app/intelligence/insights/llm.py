"""Unified LLM layer built on ai-sdk-python.

Provides provider-agnostic model initialization and convenience wrappers
for generating insight reports and note groupings.
"""
from __future__ import annotations

import logging
from typing import TypeVar

from ai_sdk import generate_object, generate_text, stream_text, tool
from ai_sdk.providers.language_model import LanguageModel
from ai_sdk.providers.openai import OpenAIModel
import openai as _openai_lib

from app.config import settings
from app.intelligence.insights.schemas_ai import (
    InsightReportOutput,
    NoteGroupListOutput,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Provider / Model Factory ───────────────────────────


def _resolve_api_key() -> str:
    """Resolve API key with fallback chain."""
    if settings.AI_SDK_API_KEY:
        return settings.AI_SDK_API_KEY
    if settings.AI_SDK_PROVIDER == "openrouter" and settings.OPENROUTER_API_KEY:
        return settings.OPENROUTER_API_KEY
    if settings.AI_SDK_PROVIDER == "openai" and settings.OPENAI_API_KEY:
        return settings.OPENAI_API_KEY
    if settings.AI_SDK_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        return settings.ANTHROPIC_API_KEY
    return settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY or ""


def _resolve_base_url() -> str | None:
    """Resolve base URL (only needed for OpenRouter / custom endpoints)."""
    if settings.AI_SDK_BASE_URL:
        return settings.AI_SDK_BASE_URL
    if settings.AI_SDK_PROVIDER == "openrouter":
        return settings.OPENROUTER_BASE_URL
    return None


def get_model(
    model_name: str | None = None,
    provider: str | None = None,
) -> LanguageModel:
    """Create an AI SDK model instance based on config.

    Supports openai, anthropic, google, and openrouter (via OpenAI-compatible endpoint).
    """
    provider = provider or settings.AI_SDK_PROVIDER
    model_name = model_name or settings.AI_SDK_MODEL
    api_key = _resolve_api_key()

    if provider == "anthropic":
        from ai_sdk import anthropic
        return anthropic(model_name, api_key=api_key)

    if provider in ("openai", "openrouter"):
        base_url = _resolve_base_url()
        if base_url:
            # OpenRouter or custom endpoint: create OpenAIModel with custom client
            model = OpenAIModel(model_name, api_key=api_key)
            model._client = _openai_lib.OpenAI(api_key=api_key, base_url=base_url)
            return model
        from ai_sdk import openai
        return openai(model_name, api_key=api_key)

    # Fallback: treat as OpenAI-compatible
    from ai_sdk import openai
    return openai(model_name, api_key=api_key)


def get_agent_model() -> LanguageModel:
    """Get model for agent workflows (workspace-agent, multi-agent).

    Uses AGENT_MODEL config which may differ from the default AI_SDK_MODEL.
    """
    agent_model = settings.AGENT_MODEL
    # AGENT_MODEL is in "provider/model" format (e.g. "anthropic/claude-sonnet-4")
    if "/" in agent_model:
        # Route through OpenRouter which understands provider/model format
        return get_model(model_name=agent_model, provider="openrouter")
    return get_model(model_name=agent_model)


# ── Convenience Wrappers ───────────────────────────────


async def generate_report(
    *,
    system: str,
    user_prompt: str,
    model: LanguageModel | None = None,
) -> InsightReportOutput:
    """Generate a structured insight report via generate_object."""
    model = model or get_model()
    result = generate_object(
        model=model,
        schema=InsightReportOutput,
        system=system,
        prompt=user_prompt,
    )
    return result.object


async def generate_groups(
    *,
    system: str,
    user_prompt: str,
    model: LanguageModel | None = None,
) -> NoteGroupListOutput:
    """Generate note groupings via generate_object."""
    model = model or get_agent_model()
    result = generate_object(
        model=model,
        schema=NoteGroupListOutput,
        system=system,
        prompt=user_prompt,
    )
    return result.object


async def stream_and_broadcast(
    *,
    system: str,
    user_prompt: str,
    generation_id: str,
    stream_prefix: str = "",
    model: LanguageModel | None = None,
) -> str:
    """Stream text generation and broadcast tokens via SSE.

    Returns the collected full text.
    """
    from app.intelligence.insights.service import broadcast_log

    model = model or get_agent_model()
    collected = ""

    result = stream_text(
        model=model,
        system=system,
        prompt=user_prompt,
    )

    async for chunk in result.text_stream:
        collected += chunk
        await broadcast_log(generation_id, {
            "type": "token",
            "token": chunk,
            "prefix": stream_prefix,
        })

    return collected


async def stream_messages_and_broadcast(
    *,
    messages: list[dict],
    generation_id: str,
    stream_prefix: str = "",
    model: LanguageModel | None = None,
) -> str:
    """Stream a multi-turn conversation and broadcast tokens via SSE.

    Uses the model's underlying OpenAI client for full messages support.
    Returns the collected full text.
    """
    from app.intelligence.insights.service import broadcast_log

    model = model or get_agent_model()

    # Use the underlying OpenAI client directly for message-based streaming
    client = getattr(model, "_client", None)
    if client is None:
        raise RuntimeError("Model does not expose an OpenAI client; cannot stream messages")

    model_id = getattr(model, "model_id", settings.AGENT_MODEL)
    collected = ""

    stream = client.chat.completions.create(
        model=model_id,
        messages=messages,
        max_tokens=settings.AGENT_MAX_TOKENS_PER_TURN,
        temperature=0.7,
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        token = delta.content if delta and delta.content else ""
        if token:
            collected += token
            await broadcast_log(generation_id, {
                "type": "token",
                "token": token,
                "prefix": stream_prefix,
            })

    return collected
