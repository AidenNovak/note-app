"""Tests for Personal Access Tokens (PAT) / CLI auth.

Covers:
  - POST /api/v1/tokens creates + returns plaintext once
  - GET /api/v1/tokens lists without exposing hash
  - PATCH /api/v1/tokens/{id} renames
  - DELETE /api/v1/tokens/{id} revokes
  - PAT Authorization header authenticates /auth/me
  - PAT write-scope enforcement on mutating requests
  - Revoked / expired tokens are rejected
  - PATs cannot be used to manage PATs (session required)
  - Invalid / malformed tokens return 401
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.utils import (
    generate_api_token,
    hash_api_token,
    normalize_scopes,
    scope_satisfies,
)
from app.models import ApiToken


# ── unit: helpers ─────────────────────────────────────────────────────────


def test_generate_api_token_shape():
    plain, prefix, token_hash = generate_api_token()
    assert plain.startswith("atl_")
    assert prefix.startswith("atl_") and len(prefix) == len("atl_") + 8
    assert plain.startswith(prefix)
    assert len(token_hash) == 64  # sha256 hex
    assert hash_api_token(plain) == token_hash


def test_normalize_scopes_dedupes_and_orders():
    assert normalize_scopes("read write read") == "write read"
    assert normalize_scopes(["admin", "read"]) == "admin read"
    assert normalize_scopes("write") == "write"
    assert normalize_scopes(None) == "read"
    assert normalize_scopes("") == "read"


def test_normalize_scopes_rejects_unknown():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        normalize_scopes("read evil")


def test_scope_satisfies_implications():
    assert scope_satisfies("admin", "write")
    assert scope_satisfies("admin", "read")
    assert scope_satisfies("write", "read")
    assert not scope_satisfies("read", "write")
    assert not scope_satisfies("read", "admin")
    assert scope_satisfies("write read", "write")


# ── integration: routes ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_tokens(client, auth_headers):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "CLI laptop", "scopes": "write", "expires_in_days": 30},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "CLI laptop"
    assert data["scopes"] == "write"
    assert data["token"].startswith("atl_")
    assert data["token_prefix"].startswith("atl_")
    assert data["expires_at"] is not None
    assert data["revoked_at"] is None
    token_id = data["id"]

    # List hides the plaintext token entirely.
    resp = await client.get("/api/v1/tokens", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == token_id
    assert "token" not in items[0]


@pytest.mark.asyncio
async def test_pat_authenticates_requests(client, auth_headers):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "default", "scopes": "read", "expires_in_days": 90},
    )
    plain = resp.json()["token"]

    # PAT can read /auth/me
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {plain}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_pat_read_scope_blocks_writes(client, auth_headers):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "read-only", "scopes": "read", "expires_in_days": 30},
    )
    plain = resp.json()["token"]

    # Attempt to create a note with a read-only PAT.
    resp = await client.post(
        "/api/v1/notes",
        headers={"Authorization": f"Bearer {plain}"},
        json={"content": "hi"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"]["code"] == "INSUFFICIENT_SCOPE"


@pytest.mark.asyncio
async def test_pat_write_scope_allows_writes(client, auth_headers):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "writer", "scopes": "write", "expires_in_days": 30},
    )
    plain = resp.json()["token"]

    resp = await client.post(
        "/api/v1/notes",
        headers={"Authorization": f"Bearer {plain}"},
        json={"content": "from CLI"},
    )
    assert resp.status_code in (200, 201), resp.text


@pytest.mark.asyncio
async def test_revoked_token_rejected(client, auth_headers):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "soon-gone", "scopes": "read", "expires_in_days": 30},
    )
    data = resp.json()
    plain = data["token"]
    token_id = data["id"]

    resp = await client.delete(f"/api/v1/tokens/{token_id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {plain}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_rejected(client, auth_headers, db, test_user):
    # Create a token directly with an already-past expiry.
    from app.auth.utils import generate_api_token
    plain, prefix, h = generate_api_token()
    token = ApiToken(
        id="expired-1",
        user_id=test_user.id,
        name="expired",
        token_prefix=prefix,
        token_hash=h,
        scopes="read",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(token)
    await db.commit()

    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {plain}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pat_cannot_manage_tokens(client, auth_headers):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "meta", "scopes": "admin", "expires_in_days": 30},
    )
    plain = resp.json()["token"]

    # Even an admin-scope PAT must not be able to mint or list PATs.
    resp = await client.get("/api/v1/tokens", headers={"Authorization": f"Bearer {plain}"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"]["code"] == "SESSION_REQUIRED"

    resp = await client.post(
        "/api/v1/tokens",
        headers={"Authorization": f"Bearer {plain}"},
        json={"name": "nope", "scopes": "read"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invalid_atl_token_rejected(client):
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer atl_thisisnotreal1234567890"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rename_token(client, auth_headers):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "old-name", "scopes": "read"},
    )
    token_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/tokens/{token_id}",
        headers=auth_headers,
        json={"name": "new-name"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "new-name"


@pytest.mark.asyncio
async def test_token_last_used_updates(client, auth_headers, db):
    resp = await client.post(
        "/api/v1/tokens",
        headers=auth_headers,
        json={"name": "last-used", "scopes": "read"},
    )
    plain = resp.json()["token"]
    token_id = resp.json()["id"]

    # Freshly minted — no last_used_at yet.
    row = (await db.execute(select(ApiToken).where(ApiToken.id == token_id))).scalar_one()
    assert row.last_used_at is None

    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {plain}"})
    assert resp.status_code == 200

    # Expire the stale session so the next read is fresh.
    db.expire_all()
    row = (await db.execute(select(ApiToken).where(ApiToken.id == token_id))).scalar_one()
    assert row.last_used_at is not None
