"""
R2-compatible file storage via S3 API (boto3).
Upload, delete, and serve files through cdn.jilly.app.
"""

from __future__ import annotations

import re
import time
import uuid
from io import BytesIO
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import User
from app.auth.utils import get_current_user

router = APIRouter(prefix="/storage", tags=["storage"])

Purpose = Literal["avatar", "attachment"]

PURPOSE_PREFIXES: dict[str, str] = {
    "avatar": "avatars",
    "attachment": "attachments",
}

ALLOWED_TYPES: dict[str, set[str]] = {
    "avatar": {"image/jpeg", "image/png", "image/gif", "image/webp"},
    "attachment": {"image/jpeg", "image/png", "image/gif", "image/webp",
                   "application/pdf", "text/plain"},
}

SIZE_LIMITS: dict[str, int] = {
    "avatar": 5 * 1024 * 1024,       # 5 MB
    "attachment": 25 * 1024 * 1024,   # 25 MB
}

MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
    "text/plain": "txt",
}


def _get_s3_client():
    """Create a boto3 S3 client for R2."""
    try:
        import boto3
    except ImportError:
        raise HTTPException(status_code=501, detail="boto3 not installed")

    if not settings.R2_ACCESS_KEY_ID:
        raise HTTPException(status_code=501, detail="R2 storage not configured")

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def _build_key(purpose: str, user_id: str, filename: str) -> str:
    """Build storage key: {prefix}/{user_id}/{timestamp}.{ext}"""
    prefix = PURPOSE_PREFIXES.get(purpose, "files")
    ext = ""
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        ext = re.sub(r"[^a-z0-9_-]", "", ext)[:10]
    if not ext:
        ext = "bin"
    timestamp = int(time.time() * 1000)
    return f"{prefix}/{user_id}/{timestamp}.{ext}"


def _get_public_url(key: str) -> str:
    """Return the CDN URL for a stored file."""
    base = settings.CDN_BASE_URL.rstrip("/")
    return f"{base}/{key}"


def _get_user_prefixes(user_id: str) -> list[str]:
    """Return all possible storage prefixes for a user (ownership check)."""
    return [
        f"avatars/{user_id}/",
        f"attachments/{user_id}/",
        f"files/{user_id}/",
    ]


def _key_from_url(url: str) -> str | None:
    """Extract storage key from a CDN URL."""
    base = settings.CDN_BASE_URL.rstrip("/")
    if url.startswith(base):
        return url[len(base):].lstrip("/")
    # Also handle api.jilly.app/api/storage/ legacy URLs
    for pattern in ["/api/storage/"]:
        idx = url.find(pattern)
        if idx >= 0:
            return url[idx + len(pattern):]
    return None


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    purpose: Purpose = Form("attachment"),
    current_user: User = Depends(get_current_user),
):
    """Upload a file to R2 storage."""
    content_type = file.content_type or "application/octet-stream"
    allowed = ALLOWED_TYPES.get(purpose, ALLOWED_TYPES["attachment"])
    if content_type not in allowed:
        raise HTTPException(status_code=400, detail=f"File type {content_type} not allowed for {purpose}")

    max_size = SIZE_LIMITS.get(purpose, SIZE_LIMITS["attachment"])
    data = await file.read()
    if len(data) > max_size:
        raise HTTPException(status_code=400, detail=f"File too large. Max {max_size // (1024*1024)} MB")

    key = _build_key(purpose, current_user.id, file.filename or "file")
    s3 = _get_s3_client()

    s3.upload_fileobj(
        BytesIO(data),
        settings.R2_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": content_type},
    )

    url = _get_public_url(key)
    return {
        "url": url,
        "key": key,
        "size": len(data),
        "content_type": content_type,
    }


@router.post("/delete")
async def delete_file(
    url: str = Form(...),
    current_user: User = Depends(get_current_user),
):
    """Delete a file from R2 storage. Only the owner can delete their files."""
    key = _key_from_url(url)
    if not key:
        raise HTTPException(status_code=400, detail="Invalid file URL")

    # Ownership check
    user_prefixes = _get_user_prefixes(current_user.id)
    if not any(key.startswith(prefix) for prefix in user_prefixes):
        raise HTTPException(status_code=403, detail="Not authorized to delete this file")

    s3 = _get_s3_client()
    s3.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)

    return {"success": True}
