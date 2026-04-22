"""Backfill embeddings after Cloudflare Workers AI migration.

Generates embeddings for all notes that currently have no entry in
note_embeddings. Run this after deploying the migration:

    cd backend
    DATABASE_URL=<your-db-url> CF_API_TOKEN=<token> CF_ACCOUNT_ID=<id> \\
        python scripts/backfill_embeddings.py

Environment variables (same as the app):
  DATABASE_URL    — SQLAlchemy async DB URL (sqlite or postgresql+asyncpg)
  CF_API_TOKEN    — Cloudflare API token  (required when AI_PROVIDER=cloudflare)
  CF_ACCOUNT_ID   — Cloudflare account ID (required when AI_PROVIDER=cloudflare)
  AI_PROVIDER     — "cloudflare" (default) or "openrouter"
  EMBEDDING_MODEL — override the embedding model (optional)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure the project root is on sys.path when run from within /backend
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Note, NoteEmbedding
from app.intelligence.embeddings import update_note_embedding, recompute_similarities

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_embeddings")

BATCH_SIZE = 20
CONCURRENCY = 4  # simultaneous embedding calls


async def _get_notes_without_embeddings(session: AsyncSession) -> list[Note]:
    stmt = (
        select(Note)
        .outerjoin(NoteEmbedding, NoteEmbedding.note_id == Note.id)
        .where(NoteEmbedding.id.is_(None), Note.content.isnot(None))
        .order_by(Note.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _process_note(session: AsyncSession, note: Note, sem: asyncio.Semaphore) -> bool:
    async with sem:
        try:
            content = (note.content or "").strip()
            if not content:
                logger.info("  skip (empty content): %s", note.id)
                return False
            await update_note_embedding(session, note.id, content)
            await recompute_similarities(session, note.id, note.user_id)
            logger.info("  embedded: %s (%.60s…)", note.id, content.replace("\n", " "))
            return True
        except Exception:
            logger.exception("  FAILED: %s", note.id)
            return False


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]

    async with async_session() as session:
        notes = await _get_notes_without_embeddings(session)

    if not notes:
        logger.info("All notes already have embeddings — nothing to do.")
        return

    logger.info("Found %d notes without embeddings (model=%s)", len(notes), settings.EMBEDDING_MODEL)

    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0
    failed = 0

    for i in range(0, len(notes), BATCH_SIZE):
        batch = notes[i : i + BATCH_SIZE]
        logger.info("Batch %d/%d …", i // BATCH_SIZE + 1, (len(notes) + BATCH_SIZE - 1) // BATCH_SIZE)
        async with async_session() as session:
            tasks = [_process_note(session, note, sem) for note in batch]
            results = await asyncio.gather(*tasks)
        done += sum(1 for r in results if r)
        failed += sum(1 for r in results if not r)

    logger.info("Done — embedded: %d, failed: %d, total: %d", done, failed, len(notes))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
