"""Embedding generation and similarity computation.

Cloudflare Workers AI (default) — calls the native REST API:
  POST /accounts/{id}/ai/run/{model}
  Auth: Bearer {CF_API_TOKEN}
  Request body: {"text": "..."}
  Response: {"result": {"data": [[...floats...]]}}

OpenRouter (fallback, AI_PROVIDER=openrouter) — OpenAI-compatible endpoint:
  POST /api/v1/embeddings
  Auth: Bearer {OPENROUTER_API_KEY}
"""
from __future__ import annotations

import json
import logging
import math
import uuid

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Note, NoteEmbedding, NoteSimilarity

logger = logging.getLogger(__name__)

_TOP_K = 3


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def generate_embedding(text: str) -> list[float]:
    """Generate a vector embedding for *text* via the configured AI provider."""
    if settings.AI_PROVIDER == "openrouter":
        return await _generate_embedding_openrouter(text)
    return await _generate_embedding_cloudflare(text)


async def _generate_embedding_cloudflare(text: str) -> list[float]:
    """Call Cloudflare Workers AI native embeddings API."""
    if not settings.CF_API_TOKEN or not settings.CF_ACCOUNT_ID:
        raise RuntimeError("CF_API_TOKEN and CF_ACCOUNT_ID are required for Cloudflare embeddings")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts"
        f"/{settings.CF_ACCOUNT_ID}/ai/run/{settings.EMBEDDING_MODEL}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.CF_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"text": text[:8000]},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["result"]["data"][0]


async def _generate_embedding_openrouter(text: str) -> list[float]:
    """Call OpenRouter OpenAI-compatible embeddings endpoint (fallback)."""
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is required for OpenRouter embeddings")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.OPENROUTER_BASE_URL}/embeddings",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.EMBEDDING_MODEL,
                "input": text[:8000],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]


async def update_note_embedding(db: AsyncSession, note_id: str, content: str) -> None:
    """Generate and upsert the embedding for a single note."""
    text = content.strip()
    if not text:
        return

    try:
        embedding = await generate_embedding(text)
    except Exception:
        logger.exception("Failed to generate embedding for note %s", note_id)
        return

    result = await db.execute(
        select(NoteEmbedding).where(NoteEmbedding.note_id == note_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.embedding_json = json.dumps(embedding)
        existing.model = settings.EMBEDDING_MODEL
        existing.dimension = len(embedding)
    else:
        db.add(NoteEmbedding(
            id=str(uuid.uuid4()),
            note_id=note_id,
            embedding_json=json.dumps(embedding),
            model=settings.EMBEDDING_MODEL,
            dimension=len(embedding),
        ))

    await db.commit()


async def recompute_similarities(db: AsyncSession, note_id: str, user_id: str) -> None:
    """Recompute top-K similar notes for *note_id* against all other user notes."""
    result = await db.execute(
        select(NoteEmbedding).where(NoteEmbedding.note_id == note_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        return

    target_vec = json.loads(target.embedding_json)

    # Fetch all other embeddings for this user's notes
    all_result = await db.execute(
        select(NoteEmbedding)
        .join(Note, Note.id == NoteEmbedding.note_id)
        .where(Note.user_id == user_id, NoteEmbedding.note_id != note_id)
    )
    others = all_result.scalars().all()

    scored: list[tuple[str, float]] = []
    for other in others:
        other_vec = json.loads(other.embedding_json)
        score = cosine_similarity(target_vec, other_vec)
        scored.append((other.note_id, score))

    scored.sort(key=lambda x: -x[1])
    top = scored[:_TOP_K]

    # Delete old similarities for this note
    await db.execute(
        delete(NoteSimilarity).where(NoteSimilarity.note_id == note_id)
    )

    for similar_note_id, score in top:
        db.add(NoteSimilarity(
            id=str(uuid.uuid4()),
            note_id=note_id,
            similar_note_id=similar_note_id,
            similarity_score=round(score, 6),
        ))

    await db.commit()
