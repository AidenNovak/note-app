"""Scheduled hard-delete of users whose soft-delete retention window has expired.

GDPR / PIPL require that "delete my account" eventually results in the user's
personal data actually being removed. We keep a 30-day soft-delete grace
window (for accidental deletes + abuse investigations) and then cascade-purge
everything tied to the user, including R2 attachments.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.logging_config import logger
from app.models import (
    File,
    GroundPost,
    GroundPostLike,
    InsightReport,
    Note,
    NoteEmbedding,
    NoteLike,
    NoteTag,
    NoteVersion,
    OAuthAccount,
    PostHide,
    PostReport,
    ProcessingTask,
    SharedNote,
    User,
    UserBlock,
)
from app.storage import delete_stored_file

RETENTION_DAYS = 30
SWEEP_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours


async def _purge_user(db: AsyncSession, user: User) -> None:
    """Cascade-delete one user's data. Runs in a single transaction."""
    uid = user.id
    logger.info("hard_delete_user_start", user_id=uid)

    # 1. Delete R2/blob files best-effort BEFORE removing DB rows so we still
    #    have storage_path handy. Failures here are logged but don't block the
    #    DB purge (orphaned blobs are cheaper than orphaned PII rows).
    file_rows = (await db.execute(select(File).where(File.user_id == uid))).scalars().all()
    for f in file_rows:
        try:
            await delete_stored_file(f.storage_path)
        except Exception:  # noqa: BLE001
            logger.exception("hard_delete_blob_failed", file_id=f.id, path=f.storage_path)

    # 2. Moderation / social edges (FK to user or their posts).
    await db.execute(delete(PostReport).where(PostReport.reporter_id == uid))
    await db.execute(delete(PostHide).where(PostHide.user_id == uid))
    await db.execute(
        delete(UserBlock).where((UserBlock.blocker_id == uid) | (UserBlock.blocked_id == uid))
    )
    await db.execute(delete(GroundPostLike).where(GroundPostLike.user_id == uid))
    await db.execute(delete(NoteLike).where(NoteLike.user_id == uid))
    await db.execute(delete(SharedNote).where(SharedNote.user_id == uid))

    # Reports/likes/hides referencing the user's posts.
    post_ids = (
        await db.execute(select(GroundPost.id).where(GroundPost.user_id == uid))
    ).scalars().all()
    if post_ids:
        await db.execute(delete(PostReport).where(PostReport.post_id.in_(post_ids)))
        await db.execute(delete(PostHide).where(PostHide.post_id.in_(post_ids)))
        await db.execute(delete(GroundPostLike).where(GroundPostLike.post_id.in_(post_ids)))
    await db.execute(delete(GroundPost).where(GroundPost.user_id == uid))

    # 3. Note-scoped children (embeddings, tasks, versions, tags, attachments).
    note_ids = (await db.execute(select(Note.id).where(Note.user_id == uid))).scalars().all()
    if note_ids:
        # Remove Vectorize embeddings (best-effort — Worker handles idempotency)
        import os, httpx
        worker_url = os.environ.get("WORKER_INSIGHTS_URL", "")
        worker_key = os.environ.get("WORKER_API_KEY", "")
        if worker_url and worker_key:
            async with httpx.AsyncClient(timeout=10) as client:
                for nid in note_ids:
                    try:
                        await client.delete(
                            f"{worker_url}/embed/{nid}",
                            headers={"X-Worker-Api-Key": worker_key},
                            params={"user_id": uid},
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("vectorize_delete_failed", note_id=nid)

        await db.execute(delete(NoteEmbedding).where(NoteEmbedding.note_id.in_(note_ids)))
        await db.execute(delete(ProcessingTask).where(ProcessingTask.note_id.in_(note_ids)))
        await db.execute(delete(NoteVersion).where(NoteVersion.note_id.in_(note_ids)))
        await db.execute(delete(NoteTag).where(NoteTag.note_id.in_(note_ids)))
    await db.execute(delete(File).where(File.user_id == uid))
    await db.execute(delete(Note).where(Note.user_id == uid))
    await db.execute(delete(InsightReport).where(InsightReport.user_id == uid))

    # 4. Auth identities.
    await db.execute(delete(OAuthAccount).where(OAuthAccount.user_id == uid))

    # 5. Finally the user row itself.
    await db.delete(user)
    await db.commit()
    logger.info("hard_delete_user_done", user_id=uid)


async def sweep_once() -> int:
    """Purge all users whose `deleted_at` is older than the retention window.

    Returns number of users purged.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    purged = 0
    async with async_session() as db:
        result = await db.execute(
            select(User).where(User.deleted_at.is_not(None)).where(User.deleted_at < cutoff)
        )
        users = result.scalars().all()
        for user in users:
            try:
                await _purge_user(db, user)
                purged += 1
            except Exception:  # noqa: BLE001
                logger.exception("hard_delete_user_failed", user_id=user.id)
                await db.rollback()
    if purged:
        logger.info("hard_delete_sweep_complete", purged=purged)
    return purged


async def sweeper_loop() -> None:
    """Long-running background loop started from the FastAPI lifespan."""
    # Jitter first run a bit so multiple replicas don't stampede on boot.
    await asyncio.sleep(60)
    while True:
        try:
            await sweep_once()
        except Exception:  # noqa: BLE001
            logger.exception("hard_delete_sweep_error")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
