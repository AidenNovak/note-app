"""Backfill note embeddings into Cloudflare Vectorize.

Calls the Worker /embed endpoint for each note, which:
  1. Generates the vector via Workers AI binding (@cf/baai/bge-m3)
  2. Upserts to Vectorize (namespace=user_id)
  3. Upserts to Supabase note_embeddings (for note_collaboration.py compat)

Usage:
    cd backend
    WORKER_INSIGHTS_URL=https://... WORKER_API_KEY=... \\
    SUPABASE_URL=https://xxx.supabase.co SUPABASE_SERVICE_KEY=<service_role_key> \\
        python scripts/backfill_embeddings.py

    # Re-embed everything (even notes already in note_embeddings):
    python scripts/backfill_embeddings.py --force

    # Preview without calling Worker:
    python scripts/backfill_embeddings.py --dry-run

Environment variables:
  WORKER_INSIGHTS_URL     — deployed Worker URL (required)
  WORKER_API_KEY          — shared secret matching Worker BACKEND_API_KEY (required)
  SUPABASE_URL            — Supabase project URL, e.g. https://xxxx.supabase.co (required)
  SUPABASE_SERVICE_KEY    — Supabase service_role key (required)

Flags:
  --force       Re-embed all notes regardless of existing embeddings
  --dry-run     Print what would be embedded without calling the Worker
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_embeddings")

BATCH_SIZE = 50
CONCURRENCY = 8   # simultaneous Worker /embed calls
TIMEOUT_SEC = 30
SUPABASE_PAGE = 1000  # rows per Supabase REST page


@dataclass
class NoteRow:
    id: str
    user_id: str
    markdown_content: str
    updated_at: Optional[str]


async def fetch_notes_from_supabase(
    client: httpx.AsyncClient,
    supabase_url: str,
    service_key: str,
    force: bool,
) -> list[NoteRow]:
    """Fetch notes directly from Supabase REST API (works regardless of local DATABASE_URL)."""
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "count=none",
    }

    # Collect already-embedded note_ids to skip (non-force mode)
    embedded_ids: set[str] = set()
    if not force:
        offset = 0
        while True:
            resp = await client.get(
                f"{supabase_url}/rest/v1/note_embeddings",
                headers={**headers, "Range": f"{offset}-{offset + SUPABASE_PAGE - 1}"},
                params={"select": "note_id"},
                timeout=TIMEOUT_SEC,
            )
            resp.raise_for_status()
            rows = resp.json()
            for row in rows:
                embedded_ids.add(row["note_id"])
            if len(rows) < SUPABASE_PAGE:
                break
            offset += SUPABASE_PAGE
        logger.info("Already embedded: %d notes (will skip)", len(embedded_ids))

    # Fetch all notes with non-empty content, paginated
    notes: list[NoteRow] = []
    offset = 0
    while True:
        resp = await client.get(
            f"{supabase_url}/rest/v1/notes",
            headers={**headers, "Range": f"{offset}-{offset + SUPABASE_PAGE - 1}"},
            params={
                "select": "id,user_id,markdown_content,updated_at",
                "markdown_content": "not.is.null",
                "order": "created_at.asc",
            },
            timeout=TIMEOUT_SEC,
        )
        resp.raise_for_status()
        rows = resp.json()
        for row in rows:
            content = (row.get("markdown_content") or "").strip()
            if not content:
                continue
            if not force and row["id"] in embedded_ids:
                continue
            notes.append(NoteRow(
                id=row["id"],
                user_id=row["user_id"],
                markdown_content=content,
                updated_at=row.get("updated_at"),
            ))
        if len(rows) < SUPABASE_PAGE:
            break
        offset += SUPABASE_PAGE

    return notes


async def embed_note(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    worker_url: str,
    worker_key: str,
    note: NoteRow,
    dry_run: bool,
) -> tuple[str, bool]:
    """Call Worker /embed for one note. Returns (note_id, success)."""
    async with sem:
        if dry_run:
            logger.info("  [dry-run] would embed: %s — %.60s", note.id, note.markdown_content.replace("\n", " "))
            return note.id, True
        try:
            resp = await client.post(
                f"{worker_url}/embed",
                headers={"X-Worker-Api-Key": worker_key, "Content-Type": "application/json"},
                json={
                    "note_id": note.id,
                    "content": note.markdown_content,
                    "user_id": note.user_id,
                    "updated_at": note.updated_at,
                },
                timeout=TIMEOUT_SEC,
            )
            if resp.status_code == 200:
                return note.id, True
            logger.warning("  WARN %s — status %d: %s", note.id, resp.status_code, resp.text[:120])
            return note.id, False
        except Exception as exc:
            logger.error("  ERROR %s — %s", note.id, exc)
            return note.id, False


async def main() -> None:
    force = "--force" in sys.argv
    dry_run = "--dry-run" in sys.argv

    worker_url = os.environ.get("WORKER_INSIGHTS_URL", "").rstrip("/")
    worker_key = os.environ.get("WORKER_API_KEY", "")
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    missing = [v for v, k in [
        ("WORKER_INSIGHTS_URL", worker_url),
        ("WORKER_API_KEY", worker_key),
        ("SUPABASE_URL", supabase_url),
        ("SUPABASE_SERVICE_KEY", service_key),
    ] if not k]
    if missing:
        sys.exit(f"ERROR: Missing required env vars: {', '.join(missing)}")

    async with httpx.AsyncClient() as client:
        notes = await fetch_notes_from_supabase(client, supabase_url, service_key, force=force)

    total = len(notes)
    if total == 0:
        logger.info("Nothing to backfill — all notes already embedded. Use --force to re-embed.")
        return
        return

    logger.info(
        "Backfilling %d notes (force=%s, dry_run=%s, concurrency=%d)",
        total, force, dry_run, CONCURRENCY,
    )

    sem = asyncio.Semaphore(CONCURRENCY)
    done = failed = 0
    t0 = time.monotonic()

    async with httpx.AsyncClient() as client:
        for batch_start in range(0, total, BATCH_SIZE):
            batch = notes[batch_start : batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            logger.info("Batch %d/%d (%d notes) …", batch_num, total_batches, len(batch))

            tasks = [
                embed_note(client, sem, worker_url, worker_key, note, dry_run)
                for note in batch
            ]
            results = await asyncio.gather(*tasks)

            batch_done = sum(1 for _, ok in results if ok)
            batch_failed = sum(1 for _, ok in results if not ok)
            done += batch_done
            failed += batch_failed
            elapsed = time.monotonic() - t0
            rate = done / elapsed if elapsed > 0 else 0
            logger.info(
                "  → %d ok, %d failed | total so far: %d/%d (%.1f notes/s)",
                batch_done, batch_failed, done + failed, total, rate,
            )

    elapsed = time.monotonic() - t0
    logger.info(
        "Done in %.1fs — embedded: %d, failed: %d, total: %d",
        elapsed, done, failed, total,
    )
    if failed:
        logger.warning("%d notes failed — re-run with --force to retry them", failed)


if __name__ == "__main__":
    asyncio.run(main())
