"""Personal Access Token management.

All endpoints require an interactive session (JWT), never a PAT — this
prevents a leaked token from silently provisioning new ones. Creation returns
the plaintext token exactly once.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.utils import (
    generate_api_token,
    normalize_scopes,
    require_session,
)
from app.database import get_db
from app.models import ApiToken, User


router = APIRouter(prefix="/tokens", tags=["tokens"])


# ── Schemas ────────────────────────────────────────────────────────────────


class ApiTokenCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    scopes: str | list[str] | None = Field(
        default="read",
        description="Space-separated or list; subset of {read, write, admin}.",
    )
    expires_in_days: Optional[int] = Field(
        default=90,
        ge=1,
        le=3650,
        description="Number of days until expiry. Pass null for no expiry.",
    )


class ApiTokenRename(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)


class ApiTokenOut(BaseModel):
    id: str
    name: str
    token_prefix: str
    scopes: str
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiTokenCreateResponse(ApiTokenOut):
    token: str = Field(description="Plaintext token. Shown only once — store securely.")


def _to_out(t: ApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=t.id,
        name=t.name,
        token_prefix=t.token_prefix,
        scopes=t.scopes,
        last_used_at=t.last_used_at,
        expires_at=t.expires_at,
        revoked_at=t.revoked_at,
        created_at=t.created_at,
    )


# ── Routes ─────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=ApiTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    body: ApiTokenCreate,
    current_user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> ApiTokenCreateResponse:
    scopes = normalize_scopes(body.scopes)

    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    plaintext, prefix, token_hash = generate_api_token()

    token = ApiToken(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        name=body.name,
        token_prefix=prefix,
        token_hash=token_hash,
        scopes=scopes,
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    out = _to_out(token)
    return ApiTokenCreateResponse(**out.model_dump(), token=plaintext)


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(
    current_user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> list[ApiTokenOut]:
    result = await db.execute(
        select(ApiToken)
        .where(ApiToken.user_id == current_user.id)
        .order_by(ApiToken.created_at.desc())
    )
    return [_to_out(t) for t in result.scalars().all()]


@router.patch("/{token_id}", response_model=ApiTokenOut)
async def rename_token(
    token_id: str,
    body: ApiTokenRename,
    current_user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> ApiTokenOut:
    token = await _load_token(db, token_id, current_user.id)
    token.name = body.name
    await db.commit()
    await db.refresh(token)
    return _to_out(token)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: str,
    current_user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
):
    token = await _load_token(db, token_id, current_user.id)
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _load_token(db: AsyncSession, token_id: str, user_id: str) -> ApiToken:
    result = await db.execute(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user_id)
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "TOKEN_NOT_FOUND", "message": "Token not found"}},
        )
    return token
