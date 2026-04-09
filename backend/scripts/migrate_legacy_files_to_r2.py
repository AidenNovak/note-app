from __future__ import annotations

import argparse
import asyncio
import re

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models import File
from app.storage import delete_stored_file, read_stored_file


R2_KEY_PREFIXES = ("attachments/", "avatars/")


def _sanitize_filename(filename: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename).strip("_")
    return safe or "upload"


def _target_key(db_file: File) -> str:
    filename = _sanitize_filename(db_file.filename or "unknown")
    return f"attachments/{db_file.user_id}/{db_file.id}-{filename}"


async def _upload_to_r2(*, server_url: str, token: str, key: str, content: bytes, filename: str, content_type: str):
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{server_url.rstrip('/')}/api/storage/migrate",
            headers={"x-migration-token": token},
            data={"key": key, "contentType": content_type},
            files={"file": (filename, content, content_type)},
        )
    resp.raise_for_status()


async def main_async(args: argparse.Namespace) -> int:
    server_url = settings.EASYSTARTER_SERVER_URL
    token = settings.STORAGE_MIGRATION_TOKEN
    if not server_url:
        raise RuntimeError("EASYSTARTER_SERVER_URL is not configured")
    if not token:
        raise RuntimeError("STORAGE_MIGRATION_TOKEN is not configured")

    migrated = 0
    skipped = 0
    failed = 0

    async with async_session() as db:
        result = await db.execute(select(File).order_by(File.created_at.asc()))
        files = list(result.scalars())

        if args.limit is not None:
            files = files[: args.limit]

        for db_file in files:
            storage_path = db_file.storage_path or ""
            if storage_path.startswith(R2_KEY_PREFIXES):
                skipped += 1
                continue

            key = _target_key(db_file)
            if args.dry_run:
                migrated += 1
                continue

            try:
                content = await read_stored_file(storage_path)
                await _upload_to_r2(
                    server_url=server_url,
                    token=token,
                    key=key,
                    content=content,
                    filename=db_file.filename or "unknown",
                    content_type=db_file.mime_type or "application/octet-stream",
                )

                old_path = db_file.storage_path
                db_file.storage_path = key
                db_file.size = len(content)
                await db.commit()

                if args.delete_legacy:
                    await delete_stored_file(old_path)

                migrated += 1
            except Exception:
                await db.rollback()
                failed += 1

    print({"migrated": migrated, "skipped": skipped, "failed": failed})
    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delete-legacy", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
