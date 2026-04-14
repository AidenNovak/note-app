"""Tests for auth registration endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.factories import UserFactory


pytestmark = pytest.mark.asyncio


class TestRegister:
    """POST /api/v1/auth/register"""

    async def test_register_success(self, client: AsyncClient, mock_email):
        payload = UserFactory.register_payload()
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == payload["username"]
        assert data["email"] == payload["email"].lower()
        assert "id" in data
        assert data["email_verified"] is False

    async def test_register_duplicate_email(self, client: AsyncClient, test_user, mock_email):
        payload = UserFactory.register_payload(email=test_user.email)
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "DUPLICATE"

    async def test_register_duplicate_username(self, client: AsyncClient, test_user, mock_email):
        payload = UserFactory.register_payload(username=test_user.username)
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "DUPLICATE"

    async def test_register_weak_password(self, client: AsyncClient, mock_email):
        payload = UserFactory.register_payload(password="short")
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 422  # Pydantic validation

    async def test_register_invalid_email(self, client: AsyncClient, mock_email):
        payload = UserFactory.register_payload(email="not-an-email")
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 422

    async def test_register_sends_verification_email(self, client: AsyncClient, mock_email):
        payload = UserFactory.register_payload()
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 201
        mock_email["send"].assert_awaited_once()

    async def test_register_email_case_insensitive(self, client: AsyncClient, mock_email):
        payload = UserFactory.register_payload(email="User@EXAMPLE.COM")
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 201
        assert resp.json()["email"] == "user@example.com"


class TestLogin:
    """POST /api/v1/auth/login"""

    async def test_login_success(self, client: AsyncClient, test_user):
        resp = await client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "Password123!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, client: AsyncClient, test_user):
        resp = await client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "WrongPass123!",
        })
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_CREDENTIALS"

    async def test_login_nonexistent_user(self, client: AsyncClient):
        resp = await client.post("/api/v1/auth/login", json={
            "email": "nobody@example.com",
            "password": "Password123!",
        })
        assert resp.status_code == 401

    async def test_login_deleted_user(self, client: AsyncClient, test_user, db):
        from datetime import datetime, timezone
        test_user.deleted_at = datetime.now(timezone.utc)
        await db.commit()

        resp = await client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "Password123!",
        })
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "ACCOUNT_DELETED"

    async def test_login_email_case_insensitive(self, client: AsyncClient, test_user):
        resp = await client.post("/api/v1/auth/login", json={
            "email": "TEST@EXAMPLE.COM",
            "password": "Password123!",
        })
        assert resp.status_code == 200


class TestRefresh:
    """POST /api/v1/auth/refresh"""

    async def test_refresh_success(self, client: AsyncClient, test_user, make_tokens):
        tokens = make_tokens(test_user.id)
        resp = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

    async def test_refresh_invalid_token(self, client: AsyncClient):
        resp = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": "invalid.token.here",
        })
        assert resp.status_code == 401

    async def test_refresh_with_access_token(self, client: AsyncClient, test_user, make_tokens):
        """Using an access token as refresh token should fail."""
        tokens = make_tokens(test_user.id)
        resp = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": tokens["access_token"],
        })
        assert resp.status_code == 401

    async def test_refresh_disabled_user(self, client: AsyncClient, test_user, make_tokens, db):
        tokens = make_tokens(test_user.id)
        test_user.is_active = False
        await db.commit()

        resp = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        })
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"]["code"] == "ACCOUNT_DISABLED"


class TestMe:
    """GET /api/v1/auth/me"""

    async def test_get_me(self, client: AsyncClient, test_user, auth_headers):
        resp = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == test_user.id
        assert data["email"] == test_user.email

    async def test_get_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code in (401, 403)

    async def test_update_me(self, client: AsyncClient, test_user, auth_headers):
        resp = await client.patch("/api/v1/auth/me", headers=auth_headers, json={
            "username": "newname",
        })
        assert resp.status_code == 200
        assert resp.json()["username"] == "newname"
