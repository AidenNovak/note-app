from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from app.intelligence.ai import get_provider
from app.intelligence.ai.prompts import NOTE_METADATA_PROMPT, NOTE_REWRITE_PROMPT
from app.intelligence.ai.response_schemas import (
    NOTE_METADATA_RESPONSE_FORMAT,
    NOTE_REWRITE_RESPONSE_FORMAT,
)
from app.models import MetadataSource

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_TITLE_PREFIX_RE = re.compile(r"^#{1,6}\s+")
_NON_TITLE_PREFIXES = ("- ", "* ", "> ", "```", "1. ", "2. ", "3. ")


@dataclass
class ResolvedNoteMetadata:
    title: str
    title_source: MetadataSource
    tags: list[str]
    tag_source: MetadataSource
    markdown_content: str | None
    needs_ai_tagging: bool = False


@dataclass
class AINoteVersionPayload:
    title: str
    markdown_content: str
    tags: list[str]
    summary: str


def normalize_tags(raw_tags: Sequence[str] | None) -> list[str]:
    if not raw_tags:
        return []
    return sorted({str(tag).strip().lower() for tag in raw_tags if str(tag).strip()})


def parse_first_line_title(markdown_content: str | None) -> tuple[str | None, str | None]:
    if markdown_content is None:
        return None, None

    normalized = markdown_content.replace("\r\n", "\n").strip()
    if not normalized:
        return None, None

    lines = normalized.split("\n")
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return None, normalized

    first_line = lines[first_index].strip()
    if first_line.startswith(_NON_TITLE_PREFIXES):
        return None, normalized

    candidate = _TITLE_PREFIX_RE.sub("", first_line).strip()
    remainder = "\n".join(lines[first_index + 1 :]).strip()
    if not candidate or not remainder or len(candidate) > 120:
        return None, normalized

    return candidate, remainder


def _strip_json_fence(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_json(raw: str) -> dict[str, object]:
    payload = _strip_json_fence(raw)
    object_start = payload.find("{")
    object_end = payload.rfind("}")
    if object_start != -1 and object_end != -1 and object_end > object_start:
        payload = payload[object_start : object_end + 1]
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("AI response must be a JSON object")
    return parsed


async def _compute_anchor_tag(
    db: AsyncSession, user_id: str, content: str,
) -> tuple[str | None, list[list[str]]]:
    """Compute embedding inline, find most similar notes, pick an anchor tag.

    Returns (anchor_tag, similar_notes_tags_for_ai).
    anchor_tag is the best high-frequency tag from the most similar note,
    or None if no suitable match exists.

    Fallback: when no embeddings exist or embedding API fails, picks the
    globally most-used tag (excluding >50% generic ones) as anchor.
    """
    try:
        import json as _json
        from sqlalchemy import select as sa_select, func as sa_func
        from app.models import Note, NoteTag, NoteEmbedding
        from app.intelligence.embeddings import generate_embedding, cosine_similarity

        # Helper: pick best anchor from global tag frequency
        async def _fallback_anchor() -> tuple[str | None, list[list[str]]]:
            """Pick the most-used tag across all user notes as anchor."""
            total = (await db.execute(
                sa_select(sa_func.count(Note.id)).where(Note.user_id == user_id)
            )).scalar() or 0
            if total == 0:
                return None, []
            max_freq = total * 0.5
            freq = (await db.execute(
                sa_select(NoteTag.tag, sa_func.count(NoteTag.id))
                .join(Note, Note.id == NoteTag.note_id)
                .where(Note.user_id == user_id)
                .group_by(NoteTag.tag)
                .order_by(sa_func.count(NoteTag.id).desc())
            )).all()
            for tag, cnt in freq:
                if cnt <= max_freq:
                    return tag, []
            return None, []

        # 1. Generate embedding for the new content (not persisted here)
        try:
            new_vec = await generate_embedding(content)
        except Exception:
            logger.warning("anchor_tag: embedding generation failed, using tag-frequency fallback")
            return await _fallback_anchor()

        # 2. Load all existing embeddings for this user's notes
        rows = (await db.execute(
            sa_select(NoteEmbedding.note_id, NoteEmbedding.embedding_json)
            .join(Note, Note.id == NoteEmbedding.note_id)
            .where(Note.user_id == user_id)
        )).all()
        if not rows:
            logger.info("anchor_tag: no embeddings in DB, using tag-frequency fallback")
            return await _fallback_anchor()

        # 3. Compute cosine similarity in memory, collect top-3
        scored: list[tuple[str, float]] = []
        for note_id, emb_json in rows:
            other_vec = _json.loads(emb_json)
            score = cosine_similarity(new_vec, other_vec)
            scored.append((note_id, score))
        scored.sort(key=lambda x: -x[1])
        top3 = [(nid, s) for nid, s in scored[:3] if s > 0.3]

        if not top3:
            logger.info("anchor_tag: no notes above similarity threshold, using tag-frequency fallback")
            return await _fallback_anchor()

        top3_ids = [nid for nid, _ in top3]

        # 4. Fetch tags for top-3 similar notes
        tag_rows = (await db.execute(
            sa_select(NoteTag.note_id, NoteTag.tag)
            .where(NoteTag.note_id.in_(top3_ids))
        )).all()

        note_tag_map: dict[str, list[str]] = {}
        for note_id, tag in tag_rows:
            note_tag_map.setdefault(note_id, []).append(tag)

        similar_tags: list[list[str]] = []
        for nid in top3_ids:
            tags = note_tag_map.get(nid, [])
            if tags:
                similar_tags.append(sorted(tags))

        # 5. Pick anchor tag from the most similar note's tags
        best_note_id = top3[0][0]
        candidate_tags = note_tag_map.get(best_note_id, [])
        if not candidate_tags:
            return None, similar_tags

        # Count global frequency of each candidate tag
        freq_rows = (await db.execute(
            sa_select(NoteTag.tag, sa_func.count(NoteTag.id))
            .where(NoteTag.tag.in_(candidate_tags))
            .group_by(NoteTag.tag)
        )).all()
        tag_freq = {tag: cnt for tag, cnt in freq_rows}

        # Get total note count for this user to compute >50% threshold
        total_notes = (await db.execute(
            sa_select(sa_func.count(Note.id)).where(Note.user_id == user_id)
        )).scalar() or 1
        max_freq = total_notes * 0.5

        # Pick highest-frequency tag that isn't too generic
        anchor_tag: str | None = None
        for tag, cnt in sorted(tag_freq.items(), key=lambda x: -x[1]):
            if cnt <= max_freq:
                anchor_tag = tag
                break

        return anchor_tag, similar_tags
    except Exception:
        logger.exception("compute_anchor_tag_failed")
        return None, []


async def _generate_metadata(
    markdown_content: str,
    current_title: str | None = None,
    similar_tags: list[list[str]] | None = None,
    anchor_tag: str | None = None,
) -> dict[str, object]:
    provider = get_provider()
    user_msg: dict[str, object] = {
        "current_title": current_title,
        "content": markdown_content,
    }
    if similar_tags:
        user_msg["similar_notes_tags"] = similar_tags
    if anchor_tag:
        user_msg["anchor_tag"] = anchor_tag
    return _parse_json(
        await provider.generate(
            NOTE_METADATA_PROMPT,
            json.dumps(user_msg, ensure_ascii=False),
            profile="note_metadata",
            response_format=NOTE_METADATA_RESPONSE_FORMAT,
        )
    )


async def resolve_note_metadata(
    markdown_content: str | None,
    *,
    explicit_title: str | None = None,
    explicit_tags: Sequence[str] | None = None,
    fallback_title: str | None = None,
    fallback_title_source: MetadataSource = MetadataSource.NONE,
    fallback_tags: Sequence[str] | None = None,
    fallback_tag_source: MetadataSource = MetadataSource.NONE,
    db: AsyncSession | None = None,
    user_id: str | None = None,
    skip_ai: bool = False,
) -> ResolvedNoteMetadata:
    cleaned_content = markdown_content.strip() if markdown_content else None
    parsed_title = None

    if explicit_title and explicit_title.strip():
        title = explicit_title.strip()
        title_source = MetadataSource.HUMAN
    else:
        parsed_title, cleaned_content = parse_first_line_title(cleaned_content)
        if parsed_title:
            title = parsed_title
            title_source = MetadataSource.HUMAN
        elif fallback_title:
            title = fallback_title
            title_source = fallback_title_source
        else:
            title = ""
            title_source = MetadataSource.NONE

    if explicit_tags is not None:
        tags = normalize_tags(explicit_tags)
        tag_source = MetadataSource.HUMAN if tags else MetadataSource.NONE
    else:
        tags = normalize_tags(fallback_tags)
        tag_source = fallback_tag_source if tags else MetadataSource.NONE

    needs_ai = cleaned_content and (not title or not tags)

    if needs_ai and not skip_ai:
        try:
            anchor_tag: str | None = None
            similar_tags: list[list[str]] | None = None
            if not tags and db is not None and user_id is not None:
                anchor_tag, similar_tags = await _compute_anchor_tag(db, user_id, cleaned_content)
            ai_metadata = await _generate_metadata(
                cleaned_content, title or None,
                similar_tags=similar_tags or None,
                anchor_tag=anchor_tag,
            )
            if not title:
                ai_title = str(ai_metadata.get("title") or "").strip()
                if ai_title:
                    title = ai_title[:255]
                    title_source = MetadataSource.AI
            if not tags:
                ai_tags = normalize_tags(ai_metadata.get("tags") if isinstance(ai_metadata.get("tags"), list) else [])
                # Force-insert anchor tag (code guarantee, not AI reliance)
                if anchor_tag:
                    anchor_set = {anchor_tag.strip().lower()}
                    merged = list(anchor_set) + [t for t in ai_tags if t not in anchor_set]
                    tags = merged[:5]
                else:
                    tags = ai_tags[:5]
                if tags:
                    tag_source = MetadataSource.AI
        except Exception:
            logger.exception("note_metadata_generation_failed")

    if not title:
        title = "Untitled Note"
        title_source = MetadataSource.SYSTEM

    return ResolvedNoteMetadata(
        title=title,
        title_source=title_source,
        tags=tags,
        tag_source=tag_source,
        markdown_content=cleaned_content,
        needs_ai_tagging=bool(needs_ai and skip_ai and not tags),
    )


async def generate_ai_note_version(
    *,
    title: str,
    markdown_content: str | None,
    tags: Sequence[str],
    instructions: str | None = None,
) -> AINoteVersionPayload:
    provider = get_provider()
    payload = _parse_json(
        await provider.generate(
            NOTE_REWRITE_PROMPT,
            json.dumps(
                {
                    "title": title,
                    "content": markdown_content or "",
                    "tags": list(tags),
                    "instructions": instructions,
                },
                ensure_ascii=False,
            ),
            profile="note_rewrite",
            response_format=NOTE_REWRITE_RESPONSE_FORMAT,
        )
    )

    ai_title = str(payload.get("title") or title).strip()[:255] or title
    ai_content = str(payload.get("markdown_content") or markdown_content or "").strip()
    ai_summary = str(payload.get("summary") or "AI collaboration draft").strip()[:255] or "AI collaboration draft"
    raw_tags = payload.get("tags")
    ai_tags = normalize_tags(raw_tags if isinstance(raw_tags, list) else list(tags))

    return AINoteVersionPayload(
        title=ai_title,
        markdown_content=ai_content or (markdown_content or ""),
        tags=ai_tags,
        summary=ai_summary,
    )


def dumps_tags(tags: Sequence[str]) -> str:
    return json.dumps(list(tags), ensure_ascii=False)


def loads_tags(raw_tags: str | None) -> list[str]:
    if not raw_tags:
        return []
    try:
        parsed = json.loads(raw_tags)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_tags(parsed)
