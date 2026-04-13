"""Embedding generation and similarity computation via OpenRouter embeddings API."""
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
    """Call OpenRouter /api/v1/embeddings to get a vector for *text*."""
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

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
