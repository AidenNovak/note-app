"""Unified LLM layer for the insights pipeline.

All calls route through the configured AI provider (Cloudflare Workers AI by
default; OpenRouter as fallback — set AI_PROVIDER=openrouter to switch).
Output shape is enforced via OpenAI ``response_format`` (json_schema, guidance
only) so system prompts describe **analysis intent only** — never JSON fields.

Reasoning models (e.g. @cf/deepseek-ai/deepseek-r1-distill-qwen-32b) emit
chain-of-thought inside ``<think>…</think>`` tags which ``_ThinkBlockSplitter``
strips out and routes to the reasoning channel.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Type, TypeVar

try:
    from ai_sdk import generate_text
    from ai_sdk.providers.language_model import LanguageModel
    from ai_sdk.providers.openai import OpenAIModel
    _HAS_AI_SDK = True
except ImportError:
    LanguageModel = Any
    OpenAIModel = None
    _HAS_AI_SDK = False
import openai as _openai_lib
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings
from app.intelligence.ai.response_schemas import response_format_for
from app.intelligence.insights.schemas_ai import (
    AngleListOutput,
    InsightReportExtractionOutput,
    InsightReportOutput,
    NoteGroupListOutput,
    ShareCardOutput,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class GeneratedTextResult:
    text: str
    finish_reason: str | None = None
    usage: Any = None
    reasoning: str = ""


@dataclass
class _AIModel:
    """Lightweight wrapper carrying a model name and its sync/async OpenAI clients."""
    name: str
    sync_client: _openai_lib.OpenAI = field(repr=False)
    async_client: AsyncOpenAI = field(repr=False)


# ── Provider / Model Factory ───────────────────────────


def _make_clients(api_key: str, base_url: str) -> tuple[_openai_lib.OpenAI, AsyncOpenAI]:
    """Create a matched sync + async OpenAI client pair for the given endpoint."""
    return (
        _openai_lib.OpenAI(api_key=api_key, base_url=base_url),
        AsyncOpenAI(api_key=api_key, base_url=base_url),
    )


def get_model(model_name: str | None = None) -> _AIModel:
    """Return an _AIModel pointed at the configured provider."""
    name = model_name or settings.AI_MODEL
    sync_c, async_c = _make_clients(settings.ai_api_key, settings.ai_base_url)
    return _AIModel(name=name, sync_client=sync_c, async_client=async_c)


def get_agent_model() -> _AIModel:
    return get_model()


def get_insights_model() -> _AIModel:
    """Reasoning-capable model for the insights pipeline.

    Falls back to AI_MODEL when INSIGHTS_AI_MODEL is empty.
    """
    return get_model(model_name=settings.INSIGHTS_AI_MODEL or settings.AI_MODEL)


# ── Shared async client ────────────────────────────────
# Kept for callers that only need the async client and don't care about model.

_async_client: AsyncOpenAI | None = None


def get_async_client() -> AsyncOpenAI:
    """Return a shared AsyncOpenAI client for the configured provider."""
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(
            api_key=settings.ai_api_key,
            base_url=settings.ai_base_url,
        )
    return _async_client


def _resolve_model_id(model: "_AIModel | Any", default: str) -> str:
    """Recover the wire model id from an _AIModel wrapper or a fallback."""
    if isinstance(model, _AIModel):
        return model.name
    return getattr(model, "_model", None) or default


# ── JSON Parsing (fallback path only) ──────────────────


def _extract_json(text: str) -> str:
    """Strip markdown fences and locate the outer JSON object/array."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

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
    """Fix invalid backslash escapes that LLMs occasionally produce."""
    import re
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', s)
    s = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)
    return s


def _parse_to_model(text: str, model_class: Type[T]) -> T:
    json_str = _fix_json_escapes(_extract_json(text))
    try:
        return model_class.model_validate_json(json_str)
    except Exception:
        return model_class.model_validate(json.loads(json_str))


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _resolve_openai_client(model: "_AIModel | Any"):
    """Extract the sync OpenAI client from an _AIModel or legacy wrapper."""
    if isinstance(model, _AIModel):
        return model.sync_client
    client = getattr(model, "_client", None)
    if client is None or not hasattr(client, "chat"):
        if hasattr(model, "chat"):
            client = model
    if client is None or not hasattr(client, "chat"):
        raise RuntimeError("Model does not expose an OpenAI client")
    return client


def _generate_text_sync(
    *,
    model: "_AIModel | Any",
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    response_format: dict[str, Any] | None = None,
) -> GeneratedTextResult:
    """Generate text. When ``response_format`` is provided, bypass ai_sdk and
    use the OpenAI client directly so the schema reaches the wire."""
    use_ai_sdk = _HAS_AI_SDK and response_format is None
    if use_ai_sdk:
        result = generate_text(
            model=model,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return GeneratedTextResult(
            text=result.text or "",
            finish_reason=getattr(result, "finish_reason", None),
            usage=getattr(result, "usage", None),
        )

    client = _resolve_openai_client(model)
    payload: dict[str, Any] = {
        "model": _resolve_model_id(model, settings.AI_MODEL),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    response = client.chat.completions.create(**payload)
    choice = response.choices[0] if response.choices else None
    text = _extract_message_text(choice.message.content) if choice and getattr(choice, "message", None) else ""
    return GeneratedTextResult(
        text=text,
        finish_reason=getattr(choice, "finish_reason", None) if choice else None,
        usage=getattr(response, "usage", None),
    )


async def _generate_text_result(
    *,
    model: "_AIModel | Any",
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    response_format: dict[str, Any] | None = None,
) -> GeneratedTextResult:
    return await asyncio.to_thread(
        _generate_text_sync,
        model=model,
        system=system,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format=response_format,
    )


# ── Streaming primitive ────────────────────────────────


class _ThinkBlockSplitter:
    """Strip ``<think>...</think>`` blocks from streamed content.

    Some reasoning models (e.g. via OpenRouter) emit the chain-of-thought
    inline inside ``delta.content`` between ``<think>`` tags rather than via
    a separate ``delta.reasoning`` field. This splitter is fed chunks
    incrementally and yields ``(content_part, reasoning_part)`` pairs,
    correctly handling tags that straddle chunk boundaries.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self.in_think = False
        self.buf = ""  # tail bytes that might be a partial tag

    @staticmethod
    def _max_partial_at_end(text: str, target: str) -> int:
        for n in range(min(len(text), len(target) - 1), 0, -1):
            if text.endswith(target[:n]):
                return n
        return 0

    def feed(self, chunk: str) -> tuple[str, str]:
        text = self.buf + chunk
        self.buf = ""
        content_out: list[str] = []
        reasoning_out: list[str] = []
        while text:
            if not self.in_think:
                idx = text.find(self.OPEN)
                if idx >= 0:
                    if idx:
                        content_out.append(text[:idx])
                    text = text[idx + len(self.OPEN):]
                    self.in_think = True
                    continue
                tail = self._max_partial_at_end(text, self.OPEN)
                if tail:
                    content_out.append(text[:-tail])
                    self.buf = text[-tail:]
                else:
                    content_out.append(text)
                text = ""
            else:
                idx = text.find(self.CLOSE)
                if idx >= 0:
                    if idx:
                        reasoning_out.append(text[:idx])
                    text = text[idx + len(self.CLOSE):]
                    self.in_think = False
                    continue
                tail = self._max_partial_at_end(text, self.CLOSE)
                if tail:
                    reasoning_out.append(text[:-tail])
                    self.buf = text[-tail:]
                else:
                    reasoning_out.append(text)
                text = ""
        return "".join(content_out), "".join(reasoning_out)

    def flush(self) -> tuple[str, str]:
        if not self.buf:
            return "", ""
        leftover = self.buf
        self.buf = ""
        if self.in_think:
            return "", leftover
        return leftover, ""


def _stream_text_sync(
    *,
    model: "_AIModel | Any",
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    model_name: str | None = None,
    on_delta=None,
) -> GeneratedTextResult:
    """Stream a single completion. No ``response_format`` — model is free.

    ``on_delta(content_delta, reasoning_delta)`` is invoked synchronously
    for each chunk that produces text. Either part may be empty. Returns
    the accumulated final result with ``reasoning`` populated.
    """
    client = _resolve_openai_client(model)
    model_id = _resolve_model_id(model, model_name or settings.AI_MODEL)

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    # OpenRouter-specific: surface reasoning chain-of-thought delta.
    # CF Workers AI uses <think>…</think> tags handled by _ThinkBlockSplitter.
    if settings.AI_PROVIDER == "openrouter":
        payload["extra_body"] = {"reasoning": {"enabled": True}}

    splitter = _ThinkBlockSplitter()
    full_content: list[str] = []
    full_reasoning: list[str] = []
    finish_reason: str | None = None
    usage: Any = None

    stream = client.chat.completions.create(**payload)
    for chunk in stream:
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = chunk_usage
        if not getattr(chunk, "choices", None):
            continue
        choice = chunk.choices[0]
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue

        # Some providers expose reasoning under different attribute names.
        reasoning_explicit = (
            getattr(delta, "reasoning", None)
            or getattr(delta, "reasoning_content", None)
            or ""
        )
        content_raw = getattr(delta, "content", None) or ""
        content_part, reasoning_inline = splitter.feed(content_raw)
        reasoning_total = (reasoning_explicit or "") + reasoning_inline

        if content_part:
            full_content.append(content_part)
        if reasoning_total:
            full_reasoning.append(reasoning_total)
        if on_delta and (content_part or reasoning_total):
            on_delta(content_part, reasoning_total)

    flushed_content, flushed_reasoning = splitter.flush()
    if flushed_content:
        full_content.append(flushed_content)
    if flushed_reasoning:
        full_reasoning.append(flushed_reasoning)
    if on_delta and (flushed_content or flushed_reasoning):
        on_delta(flushed_content, flushed_reasoning)

    return GeneratedTextResult(
        text="".join(full_content),
        reasoning="".join(full_reasoning),
        finish_reason=finish_reason,
        usage=usage,
    )


async def _stream_text_result(
    *,
    model: "_AIModel | Any",
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    model_name: str | None = None,
    on_delta=None,
) -> GeneratedTextResult:
    """Async wrapper around ``_stream_text_sync``.

    ``on_delta`` may be either a sync callable ``(content, reasoning) -> None``
    or an async coroutine; the wrapper dispatches deltas back onto the
    event loop so async callbacks (e.g. ``broadcast_log``) can ``await``.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL_DONE = "__done__"
    SENTINEL_ERROR = "__error__"

    def _push_delta(content: str, reasoning: str) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait, ("delta", content, reasoning)
        )

    def _runner() -> None:
        try:
            result = _stream_text_sync(
                model=model,
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                model_name=model_name,
                on_delta=_push_delta,
            )
            loop.call_soon_threadsafe(queue.put_nowait, (SENTINEL_DONE, result))
        except BaseException as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, (SENTINEL_ERROR, exc))

    task = asyncio.create_task(asyncio.to_thread(_runner))
    try:
        while True:
            item = await queue.get()
            kind = item[0]
            if kind == SENTINEL_DONE:
                return item[1]
            if kind == SENTINEL_ERROR:
                raise item[1]
            _, content, reasoning = item
            if on_delta is None:
                continue
            res = on_delta(content, reasoning)
            if asyncio.iscoroutine(res):
                await res
    finally:
        if not task.done():
            await task


async def stream_text_async(
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    model_name: str | None = None,
    on_delta=None,
) -> GeneratedTextResult:
    """True async streaming using AsyncOpenAI.

    No threads, no queues — ``async for chunk`` directly on the HTTP response.
    Deltas are forwarded to ``on_delta`` immediately with minimal overhead.
    """
    client = get_async_client()
    model_id = model_name or settings.AI_MODEL

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    # OpenRouter-specific reasoning delta; CF uses <think> tags in content.
    if settings.AI_PROVIDER == "openrouter":
        payload["extra_body"] = {"reasoning": {"enabled": True}}

    splitter = _ThinkBlockSplitter()
    full_content: list[str] = []
    full_reasoning: list[str] = []
    finish_reason: str | None = None
    usage: Any = None

    stream = await client.chat.completions.create(**payload)
    async for chunk in stream:
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = chunk_usage
        if not getattr(chunk, "choices", None):
            continue
        choice = chunk.choices[0]
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue

        reasoning_explicit = (
            getattr(delta, "reasoning", None)
            or getattr(delta, "reasoning_content", None)
            or ""
        )
        content_raw = getattr(delta, "content", None) or ""
        content_part, reasoning_inline = splitter.feed(content_raw)
        reasoning_total = (reasoning_explicit or "") + reasoning_inline

        if content_part:
            full_content.append(content_part)
        if reasoning_total:
            full_reasoning.append(reasoning_total)
        if on_delta and (content_part or reasoning_total):
            if asyncio.iscoroutinefunction(on_delta):
                await on_delta(content_part, reasoning_total)
            else:
                on_delta(content_part, reasoning_total)

    flushed_content, flushed_reasoning = splitter.flush()
    if flushed_content:
        full_content.append(flushed_content)
    if flushed_reasoning:
        full_reasoning.append(flushed_reasoning)
    if on_delta and (flushed_content or flushed_reasoning):
        if asyncio.iscoroutinefunction(on_delta):
            await on_delta(flushed_content, flushed_reasoning)
        else:
            on_delta(flushed_content, flushed_reasoning)

    return GeneratedTextResult(
        text="".join(full_content),
        reasoning="".join(full_reasoning),
        finish_reason=finish_reason,
        usage=usage,
    )


# ── Response format builders (strict=False; schema acts as guidance,
#    parser handles minor drift). ───────────────────────

_REPORT_RESPONSE_FORMAT = response_format_for(
    InsightReportOutput, name="insight_report", strict=False
).to_openai_payload()
_GROUPS_RESPONSE_FORMAT = response_format_for(
    NoteGroupListOutput, name="note_groups", strict=False
).to_openai_payload()
_ANGLES_RESPONSE_FORMAT = response_format_for(
    AngleListOutput, name="analysis_angles", strict=False
).to_openai_payload()


# ── Convenience Wrappers ───────────────────────────────


async def generate_report(
    *,
    system: str,
    user_prompt: str,
    model: "_AIModel | None" = None,
) -> InsightReportOutput:
    model = model or get_model()
    result = await _generate_text_result(
        model=model,
        system=system,
        prompt=user_prompt,
        max_tokens=settings.AI_MAX_TOKENS,
        temperature=settings.AI_TEMPERATURE,
        response_format=_REPORT_RESPONSE_FORMAT,
    )
    logger.info("generate_report: finish_reason=%s, text_len=%d, usage=%s",
                result.finish_reason, len(result.text or ""), result.usage)
    if not result.text:
        raise RuntimeError(f"AI provider returned empty response (finish_reason={result.finish_reason})")
    return _parse_to_model(result.text, InsightReportOutput)


async def generate_groups(
    *,
    system: str,
    user_prompt: str,
    model: "_AIModel | None" = None,
) -> NoteGroupListOutput:
    model = model or get_agent_model()
    result = await _generate_text_result(
        model=model,
        system=system,
        prompt=user_prompt,
        max_tokens=settings.AI_MAX_TOKENS,
        temperature=settings.AI_TEMPERATURE,
        response_format=_GROUPS_RESPONSE_FORMAT,
    )
    if not result.text:
        raise RuntimeError(f"AI provider returned empty response (finish_reason={result.finish_reason})")
    return _parse_to_model(result.text, NoteGroupListOutput)


# ── Clustered Pipeline: Angle Discovery ────────────────

ANGLE_DISCOVERY_SYSTEM = """\
你是一个知识分析专家。给定用户笔记的聚类摘要，从中发现 {num_angles} 个有价值的分析角度。

要求：
1. 每个角度必须基于多条笔记之间的联系，而非单条笔记的内容。
2. 角度多样化——涵盖模式发现、隐藏联系、知识空白、趋势变化、跨域综合等不同类型。
3. 每个角度选择 5-15 条最相关的笔记 ID。
4. 优先发现**非显而易见**的洞察角度。
5. 用中文输出。

`type_hint` 选值：
- pattern: 反复出现的行为/思维模式
- connection: 看似无关的笔记之间的隐藏联系
- gap: 知识或实践中的空白与矛盾
- trend: 随时间演变的趋势和变化
- synthesis: 跨多个领域的综合分析
"""


async def discover_angles(
    *,
    cluster_summaries: str,
    num_angles: int = 4,
    model: "_AIModel | None" = None,
) -> AngleListOutput:
    """Use LLM to discover analysis angles from cluster summaries."""
    model = model or get_model()
    system = ANGLE_DISCOVERY_SYSTEM.format(num_angles=num_angles)

    result = await _generate_text_result(
        model=model,
        system=system,
        prompt=cluster_summaries,
        max_tokens=settings.AI_MAX_TOKENS,
        temperature=0.8,  # slightly higher for creative angle discovery
        response_format=_ANGLES_RESPONSE_FORMAT,
    )
    if not result.text:
        raise RuntimeError(f"Angle discovery returned empty (finish_reason={result.finish_reason})")

    logger.info("discover_angles: text_len=%d, usage=%s", len(result.text), result.usage)
    return _parse_to_model(result.text, AngleListOutput)


# ── Dynamic Angle Discovery (new parallel pipeline) ────

ANGLE_DISCOVERY_FROM_NOTES_SYSTEM = """\
你是一位洞察分析师。分析用户的笔记内容，发现最有价值的分析角度。

要求：
1. **基于笔记实际内容**选择角度，不要套用固定框架。
2. 角度要有**差异性**——每个角度应该探索不同的维度，避免重复。
3. 根据笔记数量和内容的丰富程度，动态决定角度数量：
   - 内容丰富、涉及多个领域 → 3-4 个角度
   - 内容较单一 → 2 个角度
   - 笔记很少（<5 条）→ 1-2 个角度
4. 每个角度包含：
   - angle_name: 角度名称，2-6 个字，简洁有力
   - description: 1-2 句话，说明这个角度要探索什么问题
   - type_hint: 报告类型，可选值：pattern | connection | gap | trend | synthesis
5. 优先发现**非显而易见**的洞察角度，不要只停留在表面总结。
6. 如果笔记缺乏时间信息，不要硬凑 trend 角度。
7. 用中文输出。
"""


async def discover_angles_from_notes(
    *,
    notes_content: str,
    note_count: int,
    model: "_AIModel | None" = None,
) -> AngleListOutput:
    """Discover analysis angles directly from raw notes (no clustering).

    Lightweight phase-0 call: fast, no streaming, limited tokens.
    Falls back to default angles if discovery fails.
    """
    model = model or get_model()
    system = ANGLE_DISCOVERY_FROM_NOTES_SYSTEM

    prompt = (
        f"# 用户笔记（共 {note_count} 条）\n\n"
        f"{notes_content}\n\n"
        f"# 任务\n"
        f"基于以上笔记内容，发现最有价值的分析角度。"
    )

    try:
        result = await _generate_text_result(
            model=model,
            system=system,
            prompt=prompt,
            max_tokens=1200,
            temperature=0.7,
            response_format=_ANGLES_RESPONSE_FORMAT,
        )
        if not result.text:
            raise RuntimeError("Angle discovery returned empty")

        parsed = _parse_to_model(result.text, AngleListOutput)
        # Validate: at least 1 angle, at most 4
        if not parsed.angles:
            raise RuntimeError("No angles discovered")
        if len(parsed.angles) > 4:
            parsed.angles = parsed.angles[:4]

        logger.info(
            "discover_angles_from_notes: %d angles, usage=%s",
            len(parsed.angles), result.usage,
        )
        return parsed

    except Exception as exc:
        logger.warning("Angle discovery failed, will use fallback: %s", exc)
        raise


# ── Clustered Pipeline: Per-Angle Report Generation ────
#
# Two-step generation:
#   Step 1 — free-form streaming markdown write (no response_format,
#            tokens stream live to the SSE pipe so the client can render
#            both the model's thinking and the report body in real time).
#   Step 2 — one-shot strict-JSON extraction of metadata
#            (title/description/scores/evidence/actions/share_card)
#            from the markdown produced in Step 1.

ANGLE_REPORT_SYSTEM = """\
你是一位深度知识分析师。根据给定的分析角度和相关笔记，写一篇有深度的中文洞察报告。

分析角度：{angle_name} — {angle_description}
报告类型：{type_hint}

写作要求：
1. 全文用**中文**。
2. 第一段是 50-100 字的独立摘要，自然成段，不要写"摘要"二字。
3. 之后用 `## 标题` 分若干小节深入分析。
4. 在正文中自然引用具体笔记（用笔记标题或关键短语），不要只罗列。
5. 提供**真正的洞见**——发现隐藏的模式、联系或启示，不是简单汇总。
6. 直接输出 Markdown 正文，不要包裹代码块，不要多余的元数据。
"""


_REPORT_EXTRACTION_RESPONSE_FORMAT = response_format_for(
    InsightReportExtractionOutput, name="insight_report_metadata", strict=False
).to_openai_payload()


REPORT_EXTRACTION_SYSTEM = """\
你是一名信息抽取助手。基于已写好的报告 Markdown 与源笔记列表，抽取结构化元数据。

要求：
1. `title`：报告标题（≤ 30 字）。
2. `description`：1-2 句概述（50-120 字），可来自正文第一段。
3. `type` 必须是：{type_hint}
4. `evidence_items[].note_id` **只能**从下面"可用笔记 ID"列表里选，禁止编造。
5. `action_items` 给 1-3 条可执行行动（高优先在前）。
6. `share_card`：
   - eyebrow 固定 "INSIGHT REPORT"
   - headline ≤ 80 字
   - metrics 至少包含 {{label: "分析笔记数", value: "{note_count}"}}
   - footer 用 "生成于 {date}"
7. confidence/importance_score/novelty_score 给 0-1 之间的合理值。
"""


def _build_notes_index_block(note_index: list[tuple[str, str]]) -> str:
    """Render a compact id→title list to ground evidence ids in Step 2."""
    lines = [f"- {nid}: {title}" for nid, title in note_index]
    return "\n".join(lines)


def _fallback_extraction(
    markdown: str,
    *,
    angle_name: str,
    type_hint: str,
    note_count: int,
    date: str,
) -> InsightReportExtractionOutput:
    """Synthesize minimal metadata from the markdown body when Step 2 fails."""
    text = (markdown or "").strip()
    first_line = ""
    first_para = ""
    if text:
        for line in text.splitlines():
            stripped = line.strip().lstrip("# ").strip()
            if stripped:
                first_line = stripped
                break
        para_buf: list[str] = []
        for line in text.splitlines():
            if line.strip():
                if line.lstrip().startswith("#"):
                    if para_buf:
                        break
                    continue
                para_buf.append(line.strip())
            elif para_buf:
                break
        first_para = " ".join(para_buf)[:240]

    title = (first_line or angle_name or "洞察报告")[:30]
    description = (first_para or title)[:240]
    share_card = ShareCardOutput(
        headline=title[:80],
        summary=description,
        metrics=[],  # let downstream share-card builder fill defaults
        footer=f"生成于 {date}",
    )
    return InsightReportExtractionOutput(
        title=title,
        description=description,
        type=type_hint,
        confidence=0.5,
        importance_score=0.6,
        novelty_score=0.5,
        evidence_items=[],
        action_items=[],
        share_card=share_card,
    )


async def write_report_markdown(
    *,
    angle_name: str,
    angle_description: str,
    type_hint: str,
    notes_content: str,
    generation_id: str,
    group_index: int,
    model: "_AIModel | None" = None,
) -> GeneratedTextResult:
    """Step 1 — stream a free-form markdown report. Broadcasts deltas live."""
    from app.intelligence.insights.service import broadcast_log

    model = model or get_insights_model()
    system = ANGLE_REPORT_SYSTEM.format(
        angle_name=angle_name,
        angle_description=angle_description,
        type_hint=type_hint,
    )

    async def on_delta(content: str, reasoning: str) -> None:
        if reasoning:
            await broadcast_log(generation_id, {
                "type": "thinking_delta",
                "group": group_index,
                "text": reasoning,
            })
        if content:
            await broadcast_log(generation_id, {
                "type": "markdown_delta",
                "group": group_index,
                "text": content,
            })

    result = await stream_text_async(
        system=system,
        prompt=notes_content,
        max_tokens=settings.AI_MAX_TOKENS,
        temperature=settings.AI_TEMPERATURE,
        model_name=model.name if isinstance(model, _AIModel) else _resolve_model_id(model, settings.INSIGHTS_AI_MODEL or settings.AI_MODEL),
        on_delta=on_delta,
    )

    if not result.text.strip():
        raise RuntimeError(
            f"Streaming report write for '{angle_name}' produced empty markdown "
            f"(finish_reason={result.finish_reason})"
        )

    logger.info(
        "write_report_markdown[%s]: text_len=%d, reasoning_len=%d, finish=%s",
        angle_name, len(result.text), len(result.reasoning), result.finish_reason,
    )
    return result


async def extract_report_metadata(
    *,
    markdown: str,
    angle_name: str,
    type_hint: str,
    note_index: list[tuple[str, str]],
    note_count: int,
    date: str,
    model: "_AIModel | None" = None,
) -> InsightReportExtractionOutput:
    """Step 2 — one-shot strict-JSON extraction over the Step-1 markdown."""
    model = model or get_model()  # cheaper general model is fine here
    system = REPORT_EXTRACTION_SYSTEM.format(
        type_hint=type_hint,
        note_count=note_count,
        date=date,
    )
    notes_block = _build_notes_index_block(note_index)
    user_prompt = (
        "## 可用笔记 ID（evidence_items.note_id 只能用这些）\n"
        f"{notes_block}\n\n"
        "## 报告 Markdown\n"
        f"{markdown}"
    )

    try:
        result = await _generate_text_result(
            model=model,
            system=system,
            prompt=user_prompt,
            max_tokens=settings.AI_MAX_TOKENS,
            temperature=settings.AI_TEMPERATURE,
            response_format=_REPORT_EXTRACTION_RESPONSE_FORMAT,
        )
        if not result.text:
            raise RuntimeError(
                f"Extraction returned empty (finish_reason={result.finish_reason})"
            )
        extracted = _parse_to_model(result.text, InsightReportExtractionOutput)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "extract_report_metadata fallback for '%s': %s", angle_name, exc,
        )
        return _fallback_extraction(
            markdown,
            angle_name=angle_name,
            type_hint=type_hint,
            note_count=note_count,
            date=date,
        )

    # Drop hallucinated evidence note_ids that aren't in the source set.
    valid_ids = {nid for nid, _ in note_index}
    extracted.evidence_items = [
        ev for ev in extracted.evidence_items if ev.note_id in valid_ids
    ]
    if extracted.type not in {"pattern", "connection", "gap", "trend", "synthesis", "report"}:
        extracted.type = type_hint
    elif extracted.type == "report":
        extracted.type = type_hint
    return extracted


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
    model: "_AIModel | None" = None,
    note_index: list[tuple[str, str]] | None = None,
) -> InsightReportOutput:
    """Generate a single insight report for one analysis angle.

    Two-step under the hood:
      1. Stream markdown body live (``write_report_markdown``).
      2. Extract structured metadata from the markdown
         (``extract_report_metadata``).

    Returns the same ``InsightReportOutput`` shape as before so existing
    callers (pipeline persistence, scripts) are untouched.
    """
    from app.intelligence.insights.service import broadcast_log

    insights_model = model or get_insights_model()

    await broadcast_log(generation_id, {
        "type": "progress",
        "message": f"s{group_index} 正在生成报告: {angle_name}...",
    })

    write_result = await write_report_markdown(
        angle_name=angle_name,
        angle_description=angle_description,
        type_hint=type_hint,
        notes_content=notes_content,
        generation_id=generation_id,
        group_index=group_index,
        model=insights_model,
    )

    extraction = await extract_report_metadata(
        markdown=write_result.text,
        angle_name=angle_name,
        type_hint=type_hint,
        note_index=note_index or [],
        note_count=note_count,
        date=date,
    )

    await broadcast_log(generation_id, {
        "type": "decision",
        "group": group_index,
        "stage": "extraction_done",
        "payload": {
            "evidence_count": len(extraction.evidence_items),
            "action_count": len(extraction.action_items),
        },
    })

    return InsightReportOutput(
        title=extraction.title,
        description=extraction.description,
        type=extraction.type,
        report_markdown=write_result.text,
        thinking_trace=write_result.reasoning or None,
        confidence=extraction.confidence,
        importance_score=extraction.importance_score,
        novelty_score=extraction.novelty_score,
        evidence_items=extraction.evidence_items,
        action_items=extraction.action_items,
        share_card=extraction.share_card,
    )
