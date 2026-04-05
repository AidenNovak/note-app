from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from app.config import running_on_vercel, settings

try:
    from vercel.blob import AsyncBlobClient, BlobNotFoundError
except Exception:  # pragma: no cover - dependency may be missing or unavailable on older local runtimes
    AsyncBlobClient = None
    BlobNotFoundError = Exception


class FileTooLargeError(Exception):
    pass


@dataclass
class StoredFile:
    storage_path: str
    size: int


def build_storage_key(user_id: str, note_id: str | None, file_id: str, filename: str | None) -> str:
    ext = Path(filename or "unknown").suffix
    note_segment = note_id or "unattached"
    return f"users/{user_id}/notes/{note_segment}/{file_id}{ext}"


def _use_blob_storage() -> bool:
    return running_on_vercel() and bool(os.getenv("BLOB_READ_WRITE_TOKEN"))


def _blob_client() -> AsyncBlobClient:
    if AsyncBlobClient is None:
        raise RuntimeError("vercel blob SDK is not installed")
    token = os.getenv("BLOB_READ_WRITE_TOKEN")
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN must be configured on Vercel")
    return AsyncBlobClient(token=token)


async def save_upload(upload: UploadFile, storage_key: str, max_bytes: int) -> StoredFile:
    if _use_blob_storage():
        return await _save_blob_upload(upload, storage_key, max_bytes)
    return await _save_local_upload(upload, storage_key, max_bytes)


async def read_stored_file(storage_path: str) -> bytes:
    if _use_blob_storage():
        try:
            return await _blob_client().get(storage_path)
        except BlobNotFoundError as exc:
            raise FileNotFoundError(storage_path) from exc

    path = Path(storage_path)
    if not path.exists():
        raise FileNotFoundError(storage_path)
    async with aiofiles.open(path, "rb") as file_handle:
        return await file_handle.read()


async def delete_stored_file(storage_path: str) -> None:
    if _use_blob_storage():
        try:
            await _blob_client().delete(storage_path)
        except BlobNotFoundError:
            return
        return

    path = Path(storage_path)
    if path.exists():
        path.unlink()


async def _save_local_upload(upload: UploadFile, storage_key: str, max_bytes: int) -> StoredFile:
    storage_path = Path(settings.STORAGE_PATH) / storage_key
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    size = 0

    try:
        async with aiofiles.open(storage_path, "wb") as file_handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLargeError
                await file_handle.write(chunk)
    except Exception:
        if storage_path.exists():
            storage_path.unlink()
        raise
    finally:
        await upload.close()

    return StoredFile(storage_path=str(storage_path), size=size)


async def _save_blob_upload(upload: UploadFile, storage_key: str, max_bytes: int) -> StoredFile:
    size = 0
    chunks: list[bytes] = []

    try:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise FileTooLargeError
            chunks.append(chunk)
    finally:
        await upload.close()

    result = await _blob_client().put(
        storage_key,
        b"".join(chunks),
        access="private",
        content_type=upload.content_type or "application/octet-stream",
        add_random_suffix=False,
    )
    return StoredFile(storage_path=result.pathname, size=size)
