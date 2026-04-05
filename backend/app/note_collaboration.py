from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from app.intelligence.ai import get_provider
from app.intelligence.ai.prompts import NOTE_METADATA_PROMPT, NOTE_REWRITE_PROMPT
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


async def _fetch_similar_note_tags(
    db: AsyncSession, user_id: str, content: str, limit: int = 3,
) -> list[list[str]]:
    """Return tags of the top-N most similar notes using TF-IDF cosine similarity."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        logger.debug("sklearn not available, skipping similar-note tag lookup")
        return []

    try:
        from sqlalchemy import select as sa_select
        from app.models import Note, NoteTag

        rows = (await db.execute(
            sa_select(Note.id, Note.title, Note.markdown_content)
            .where(Note.user_id == user_id)
        )).all()
        if not rows:
            return []

        tag_rows = (await db.execute(
            sa_select(NoteTag.note_id, NoteTag.tag)
            .where(NoteTag.note_id.in_([r[0] for r in rows]))
        )).all()
        note_tag_map: dict[str, list[str]] = {}
        for note_id, tag in tag_rows:
            note_tag_map.setdefault(note_id, []).append(tag)

        docs: list[str] = []
        note_ids: list[str] = []
        for nid, title, body in rows:
            tags_str = " ".join(note_tag_map.get(nid, []))
            docs.append(f"{title or ''} {(body or '')[:500]} {tags_str}")
            note_ids.append(nid)

        # Append the new content as the last document
        docs.append(content[:500])

        vectorizer = TfidfVectorizer(max_features=500, stop_words="english")
        tfidf_matrix = vectorizer.fit_transform(docs)
        sim_scores = cosine_similarity(tfidf_matrix[-1:], tfidf_matrix[:-1])[0]

        top_indices = sorted(range(len(sim_scores)), key=lambda i: sim_scores[i], reverse=True)[:limit]
        result: list[list[str]] = []
        for idx in top_indices:
            tags = note_tag_map.get(note_ids[idx], [])
            if tags:
                result.append(sorted(tags))
        return result
    except Exception:
        logger.exception("fetch_similar_note_tags_failed")
        return []


async def _generate_metadata(
    markdown_content: str,
    current_title: str | None = None,
    similar_tags: list[list[str]] | None = None,
) -> dict[str, object]:
    provider = get_provider()
    user_msg: dict[str, object] = {
        "current_title": current_title,
        "content": markdown_content,
    }
    if similar_tags:
        user_msg["similar_notes_tags"] = similar_tags
    return _parse_json(
        await provider.generate(
            NOTE_METADATA_PROMPT,
            json.dumps(user_msg, ensure_ascii=False),
            profile="note_metadata",
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

    if cleaned_content and (not title or not tags):
        try:
            similar_tags: list[list[str]] | None = None
            if not tags and db is not None and user_id is not None:
                similar_tags = await _fetch_similar_note_tags(db, user_id, cleaned_content)
            ai_metadata = await _generate_metadata(cleaned_content, title or None, similar_tags=similar_tags)
            if not title:
                ai_title = str(ai_metadata.get("title") or "").strip()
                if ai_title:
                    title = ai_title[:255]
                    title_source = MetadataSource.AI
            if not tags:
                tags = normalize_tags(ai_metadata.get("tags") if isinstance(ai_metadata.get("tags"), list) else [])
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
