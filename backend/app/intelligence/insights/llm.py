"""Unified LLM layer built on ai-sdk-python.

Provides provider-agnostic model initialization and convenience wrappers
for generating insight reports and note groupings.
"""
from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from typing import Type, TypeVar

from ai_sdk import generate_text, stream_text
from ai_sdk.providers.language_model import LanguageModel
from ai_sdk.providers.openai import OpenAIModel
import openai as _openai_lib
from pydantic import BaseModel

from app.config import settings
from app.intelligence.insights.schemas_ai import (
    AngleListOutput,
    InsightReportOutput,
    NoteGroupListOutput,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


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


# ── JSON Parsing ───────────────────────────────────────


def _extract_json(text: str) -> str:
    """Extract JSON from LLM response, stripping markdown fences and surrounding text."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    # Try to find a balanced JSON object or array
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = cleaned.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    return cleaned[start:i + 1]

    return cleaned


def _fix_json_escapes(s: str) -> str:
    """Fix invalid backslash escapes that LLMs often produce in JSON strings."""
    import re
    # Strip control characters (keep \n \r \t which are valid in JSON)
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', s)
    # Fix invalid \escape sequences: replace \ not followed by valid JSON escape chars
    # Valid JSON escapes: \" \\ \/ \b \f \n \r \t \uXXXX
    s = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)
    return s


def _parse_to_model(text: str, model_class: Type[T]) -> T:
    """Parse LLM text output into a Pydantic model."""
    json_str = _extract_json(text)
    json_str = _fix_json_escapes(json_str)
    logger.debug("LLM raw text length=%d, extracted JSON length=%d", len(text), len(json_str))
    try:
        return model_class.model_validate_json(json_str)
    except Exception:
        data = json.loads(json_str)
        return model_class.model_validate(data)


# ── Convenience Wrappers ───────────────────────────────


async def generate_report(
    *,
    system: str,
    user_prompt: str,
    model: LanguageModel | None = None,
) -> InsightReportOutput:
    """Generate a structured insight report via generate_text + Pydantic parsing.

    The system prompt already describes the expected JSON schema in detail.
    We use generate_text (not generate_object) because AI SDK's schema
    instruction builder oversimplifies nested models.
    generate_text is synchronous, so we run it in a thread to avoid blocking the event loop.
    """
    model = model or get_model()
    result = await asyncio.to_thread(
        generate_text,
        model=model,
        system=system,
        prompt=user_prompt,
        max_tokens=settings.AI_SDK_MAX_TOKENS,
        temperature=settings.AI_SDK_TEMPERATURE,
    )
    logger.info("generate_report: finish_reason=%s, text_len=%d, usage=%s",
                result.finish_reason, len(result.text) if result.text else 0, result.usage)
    if not result.text:
        raise RuntimeError(f"AI provider returned empty response (finish_reason={result.finish_reason})")
    return _parse_to_model(result.text, InsightReportOutput)


async def generate_groups(
    *,
    system: str,
    user_prompt: str,
    model: LanguageModel | None = None,
) -> NoteGroupListOutput:
    """Generate note groupings via generate_text + Pydantic parsing.

    The s0 prompt describes the expected JSON array format. Handles various
    model output formats: bare array, wrapped dict, or single group object.
    generate_text is synchronous, so we run it in a thread to avoid blocking the event loop.
    """
    model = model or get_agent_model()
    result = await asyncio.to_thread(
        generate_text,
        model=model,
        system=system,
        prompt=user_prompt,
        max_tokens=settings.AI_SDK_MAX_TOKENS,
        temperature=settings.AI_SDK_TEMPERATURE,
    )
    if not result.text:
        raise RuntimeError(f"AI provider returned empty response (finish_reason={result.finish_reason})")
    json_str = _extract_json(result.text)
    data = json.loads(json_str)

    # Normalize to {"groups": [...]}}
    if isinstance(data, list):
        data = {"groups": data}
    elif isinstance(data, dict):
        # Check if it's a wrapped response like {"groups": [...]}
        for key in ("groups", "data", "result"):
            if key in data and isinstance(data[key], list):
                data = {"groups": data[key]}
                break
        else:
            # Single group object (has "angle"/"note_ids") — wrap in array
            if "angle" in data or "note_ids" in data:
                data = {"groups": [data]}

    return NoteGroupListOutput.model_validate(data)


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
    The OpenAI streaming API is synchronous, so we run it in a thread
    and collect results to avoid blocking the event loop.
    Returns the collected full text.
    """
    from app.intelligence.insights.service import broadcast_log

    model = model or get_agent_model()

    # Use the underlying OpenAI client directly for message-based streaming
    client = getattr(model, "_client", None)
    if client is None:
        raise RuntimeError("Model does not expose an OpenAI client; cannot stream messages")

    model_id = getattr(model, "_model", settings.AGENT_MODEL)

    def _sync_stream() -> list[str]:
        tokens: list[str] = []
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
                tokens.append(token)
        return tokens

    tokens = await asyncio.to_thread(_sync_stream)
    collected = ""
    for token in tokens:
        collected += token
        await broadcast_log(generation_id, {
            "type": "token",
            "token": token,
            "prefix": stream_prefix,
        })

    return collected


# ── Clustered Pipeline: Angle Discovery ────────────────

ANGLE_DISCOVERY_SYSTEM = """\
你是一个知识分析专家。给定用户笔记的聚类摘要，你需要从中发现 {num_angles} 个有价值的分析角度。

## 要求

1. 每个角度必须基于多条笔记之间的联系，而非单条笔记的内容。
2. 角度应该多样化——涵盖模式发现、隐藏联系、知识空白、趋势变化、跨域综合等不同类型。
3. 每个角度选择 5-15 条最相关的笔记 ID。
4. 优先发现**非显而易见**的洞察角度。
5. 用中文输出。

## 报告类型说明
- pattern: 发现笔记中反复出现的行为/思维模式
- connection: 发现看似无关的笔记之间的隐藏联系
- gap: 发现知识或实践中的空白与矛盾
- trend: 发现随时间演变的趋势和变化
- synthesis: 跨多个领域的综合分析

## 输出格式

返回 JSON:
```json
{{
  "angles": [
    {{
      "angle_name": "2-6字角度名称",
      "description": "1-2句话描述该角度要探索的问题",
      "note_ids": ["id1", "id2", ...],
      "type_hint": "pattern|connection|gap|trend|synthesis"
    }}
  ]
}}
```
"""


async def discover_angles(
    *,
    cluster_summaries: str,
    num_angles: int = 4,
    model: LanguageModel | None = None,
) -> AngleListOutput:
    """Use LLM to discover analysis angles from cluster summaries."""
    model = model or get_model()
    system = ANGLE_DISCOVERY_SYSTEM.format(num_angles=num_angles)

    result = await asyncio.to_thread(
        generate_text,
        model=model,
        system=system,
        prompt=cluster_summaries,
        max_tokens=settings.AI_SDK_MAX_TOKENS,
        temperature=0.8,  # slightly higher for creative angle discovery
    )
    if not result.text:
        raise RuntimeError(f"Angle discovery returned empty (finish_reason={result.finish_reason})")

    logger.info("discover_angles: text_len=%d, usage=%s", len(result.text), result.usage)

    json_str = _extract_json(result.text)
    data = json.loads(json_str)

    # Normalize: might be {"angles": [...]} or bare [...]
    if isinstance(data, list):
        data = {"angles": data}
    elif isinstance(data, dict) and "angles" not in data:
        # Maybe single angle or differently keyed
        for key in ("results", "data", "analysis"):
            if key in data and isinstance(data[key], list):
                data = {"angles": data[key]}
                break
        else:
            if "angle_name" in data:
                data = {"angles": [data]}

    return AngleListOutput.model_validate(data)


# ── Clustered Pipeline: Per-Angle Report Generation ────

ANGLE_REPORT_SYSTEM = """\
你是一位深度知识分析师。根据给定的分析角度和相关笔记，生成一篇有深度的中文洞察报告。

## 分析角度
{angle_name}: {angle_description}

## 要求

1. 报告必须用**中文**撰写。
2. 第一段必须是 50-100 字的独立摘要。
3. 后续用 ## 标题分节，深入分析。
4. 提供**真正的洞见**——不是笔记内容的简单汇总，而是发现隐藏的模式、联系和启示。
5. 引用具体笔记作为证据。
6. 提出 1-3 个可执行的行动建议。
7. 报告类型: {type_hint}

## 输出格式

返回**严格合法的** JSON（不要包含注释、末尾逗号或未转义的控制字符）:
{{
  "title": "引人入胜的报告标题",
  "description": "2-3句话的执行摘要",
  "type": "{type_hint}",
  "report_markdown": "完整的 Markdown 报告正文。第一段必须是独立的50-100字摘要。",
  "confidence": 0.0-1.0,
  "importance_score": 0.0-1.0,
  "novelty_score": 0.0-1.0,
  "evidence_items": [
    {{"note_id": "笔记ID", "quote": "引用原文", "rationale": "为什么这条证据重要"}}
  ],
  "action_items": [
    {{"title": "行动标题", "detail": "具体步骤", "priority": "high|medium|low"}}
  ],
  "share_card": {{
    "theme": "report",
    "eyebrow": "INSIGHT REPORT",
    "headline": "≤80字的标题",
    "summary": "2-3句话摘要",
    "highlight": "最惊人的发现",
    "evidence_quote": "最佳支撑引文",
    "evidence_source": "来源笔记标题",
    "action_title": "首要推荐行动",
    "action_detail": "简要细节",
    "metrics": [{{"label": "分析笔记数", "value": "{note_count}"}}],
    "footer": "生成于 {date}"
  }}
}}
"""


async def generate_report_for_angle(
    *,
    angle_name: str,
    angle_description: str,
    type_hint: str,
    notes_content: str,
    note_count: int,
    date: str,
    generation_id: str,
    group_index: int,
    model: LanguageModel | None = None,
) -> InsightReportOutput:
    """Generate a single insight report for one analysis angle.

    Uses generate_text (not streaming) for structured JSON output,
    then broadcasts completion. Token-level streaming is complex with
    JSON output so we broadcast progress events instead.
    """
    from app.intelligence.insights.service import broadcast_log

    model = model or get_model()
    system = ANGLE_REPORT_SYSTEM.format(
        angle_name=angle_name,
        angle_description=angle_description,
        type_hint=type_hint,
        note_count=note_count,
        date=date,
    )

    await broadcast_log(generation_id, {
        "type": "progress",
        "message": f"s{group_index} 正在生成报告: {angle_name}...",
    })

    result = await asyncio.to_thread(
        generate_text,
        model=model,
        system=system,
        prompt=notes_content,
        max_tokens=settings.AI_SDK_MAX_TOKENS,
        temperature=settings.AI_SDK_TEMPERATURE,
    )

    if not result.text:
        raise RuntimeError(f"Report generation for '{angle_name}' returned empty")

    logger.info(
        "generate_report_for_angle[%s]: text_len=%d, usage=%s",
        angle_name, len(result.text), result.usage,
    )

    return _parse_to_model(result.text, InsightReportOutput)
