"""Tests for OAuth sign-in (Apple, Google, GitHub) and account management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OAuthAccount, User


pytestmark = pytest.mark.asyncio


# ── Apple Sign In ────────────────────────────────────────

class TestAppleSignIn:
    """POST /api/v1/auth/apple"""

    async def test_apple_new_user(self, client: AsyncClient, db, mocker):
        mocker.patch("api.v1.auth.verify_apple_identity_token", new_callable=AsyncMock, return_value={
            "sub": "apple_001",
            "email": "apple@example.com",
            "email_verified": True,
        })

        resp = await client.post("/api/v1/auth/apple", json={
            "identity_token": "fake.apple.token",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_apple_existing_oauth(self, client: AsyncClient, test_user, db, mocker):
        """Returning Apple user should get tokens without creating new account."""
        oauth = OAuthAccount(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="apple",
            provider_account_id="apple_existing",
        )
        db.add(oauth)
        await db.commit()

        mocker.patch("api.v1.auth.verify_apple_identity_token", new_callable=AsyncMock, return_value={
            "sub": "apple_existing",
            "email": test_user.email,
            "email_verified": True,
        })

        resp = await client.post("/api/v1/auth/apple", json={
            "identity_token": "fake.apple.token",
        })
        assert resp.status_code == 200

    async def test_apple_invalid_token(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.verify_apple_identity_token", new_callable=AsyncMock, side_effect=ValueError("Invalid token"))

        resp = await client.post("/api/v1/auth/apple", json={
            "identity_token": "bad.token",
        })
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "APPLE_AUTH_FAILED"

    async def test_apple_with_full_name(self, client: AsyncClient, db, mocker):
        mocker.patch("api.v1.auth.verify_apple_identity_token", new_callable=AsyncMock, return_value={
            "sub": "apple_name_test",
            "email": "applename@example.com",
            "email_verified": True,
        })

        resp = await client.post("/api/v1/auth/apple", json={
            "identity_token": "fake.token",
            "full_name": {"givenName": "John", "familyName": "Doe"},
        })
        assert resp.status_code == 200

    async def test_apple_deleted_user(self, client: AsyncClient, test_user, db, mocker):
        """Deleted user trying to sign in via Apple should get 401."""
        oauth = OAuthAccount(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="apple",
            provider_account_id="apple_deleted",
        )
        db.add(oauth)
        test_user.deleted_at = datetime.now(timezone.utc)
        await db.commit()

        mocker.patch("api.v1.auth.verify_apple_identity_token", new_callable=AsyncMock, return_value={
            "sub": "apple_deleted",
            "email": test_user.email,
            "email_verified": True,
        })

        resp = await client.post("/api/v1/auth/apple", json={
            "identity_token": "fake.token",
        })
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "ACCOUNT_DELETED"


# ── Google Sign In ───────────────────────────────────────

class TestGoogleSignIn:
    """POST /api/v1/auth/google"""

    async def test_google_id_token_flow(self, client: AsyncClient, db, mocker):
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_ID = "test-google-id"
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_SECRET = "test-google-secret"
        mocker.patch("api.v1.auth.verify_google_id_token", new_callable=AsyncMock, return_value={
            "sub": "google_001",
            "email": "google@example.com",
            "email_verified": True,
            "name": "Google User",
            "picture": "https://example.com/avatar.jpg",
        })

        resp = await client.post("/api/v1/auth/google", json={
            "id_token": "fake.google.id_token",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_google_code_flow(self, client: AsyncClient, db, mocker):
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_ID = "test-google-id"
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_SECRET = "test-google-secret"
        mocker.patch("api.v1.auth.exchange_google_code", new_callable=AsyncMock, return_value={
            "sub": "google_002",
            "email": "google2@example.com",
            "email_verified": True,
            "name": "Google Web User",
        })

        resp = await client.post("/api/v1/auth/google", json={
            "code": "auth-code-123",
            "redirect_uri": "https://app.jilly.app/auth/callback",
        })
        assert resp.status_code == 200

    async def test_google_no_params(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_ID = "test-google-id"

        resp = await client.post("/api/v1/auth/google", json={})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "MISSING_PARAMS"

    async def test_google_code_without_redirect(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_ID = "test-google-id"
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_SECRET = "test-google-secret"

        resp = await client.post("/api/v1/auth/google", json={
            "code": "auth-code-123",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "MISSING_REDIRECT_URI"

    async def test_google_not_configured(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_ID = ""

        resp = await client.post("/api/v1/auth/google", json={
            "id_token": "fake.token",
        })
        assert resp.status_code == 501
        assert resp.json()["detail"]["error"]["code"] == "NOT_CONFIGURED"

    async def test_google_invalid_token(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.settings").GOOGLE_CLIENT_ID = "test-google-id"
        mocker.patch("api.v1.auth.verify_google_id_token", new_callable=AsyncMock, side_effect=ValueError("Invalid token"))

        resp = await client.post("/api/v1/auth/google", json={
            "id_token": "bad.token",
        })
        assert resp.status_code == 401


# ── GitHub Sign In ───────────────────────────────────────

class TestGitHubSignIn:
    """POST /api/v1/auth/github"""

    async def test_github_success(self, client: AsyncClient, db, mocker):
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_ID = "test-gh-id"
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_SECRET = "test-gh-secret"
        mocker.patch("api.v1.auth.exchange_github_code", new_callable=AsyncMock, return_value={
            "sub": "gh_001",
            "email": "ghuser@example.com",
            "email_verified": True,
            "name": "GH User",
            "login": "ghuser",
            "avatar_url": "https://avatars.githubusercontent.com/u/1",
            "access_token": "gho_test123",
        })

        resp = await client.post("/api/v1/auth/github", json={
            "code": "github-auth-code",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_github_not_configured(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_ID = ""
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_SECRET = ""

        resp = await client.post("/api/v1/auth/github", json={
            "code": "auth-code",
        })
        assert resp.status_code == 501

    async def test_github_invalid_code(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_ID = "test-gh-id"
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_SECRET = "test-gh-secret"
        mocker.patch("api.v1.auth.exchange_github_code", new_callable=AsyncMock, side_effect=ValueError("Bad code"))

        resp = await client.post("/api/v1/auth/github", json={
            "code": "invalid-code",
        })
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "GITHUB_AUTH_FAILED"

    async def test_github_account_linking(self, client: AsyncClient, test_user, db, mocker):
        """GitHub sign-in with existing email should link to existing user."""
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_ID = "test-gh-id"
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_SECRET = "test-gh-secret"
        mocker.patch("api.v1.auth.exchange_github_code", new_callable=AsyncMock, return_value={
            "sub": "gh_link_001",
            "email": test_user.email,  # same email as existing user
            "email_verified": True,
            "name": "Test User GH",
            "login": "testuser_gh",
        })

        resp = await client.post("/api/v1/auth/github", json={
            "code": "link-code",
        })
        assert resp.status_code == 200

    async def test_github_no_email(self, client: AsyncClient, db, mocker):
        """GitHub user with private email should still register."""
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_ID = "test-gh-id"
        mocker.patch("api.v1.auth.settings").GITHUB_CLIENT_SECRET = "test-gh-secret"
        mocker.patch("api.v1.auth.exchange_github_code", new_callable=AsyncMock, return_value={
            "sub": "gh_no_email",
            "email": None,
            "email_verified": False,
            "name": "Private User",
            "login": "privateuser",
        })

        resp = await client.post("/api/v1/auth/github", json={
            "code": "private-code",
        })
        assert resp.status_code == 200


# ── Password Status ──────────────────────────────────────

class TestPasswordStatus:
    """GET /api/v1/auth/password-status"""

    async def test_has_password(self, client: AsyncClient, test_user, auth_headers):
        resp = await client.get("/api/v1/auth/password-status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_password"] is True
        assert data["providers"] == []

    async def test_oauth_only_no_password(self, client: AsyncClient, test_user, auth_headers, db):
        test_user.hashed_password = "!"
        oauth = OAuthAccount(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="google",
            provider_account_id="google_status_test",
        )
        db.add(oauth)
        await db.commit()

        resp = await client.get("/api/v1/auth/password-status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_password"] is False
        assert "google" in data["providers"]

    async def test_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/auth/password-status")
        assert resp.status_code in (401, 403)


# ── Account Deletion ─────────────────────────────────────

class TestDeleteAccount:
    """DELETE /api/v1/auth/account"""

    async def test_delete_success(self, client: AsyncClient, test_user, auth_headers, db):
        resp = await client.delete("/api/v1/auth/account", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        await db.refresh(test_user)
        assert test_user.deleted_at is not None
        assert test_user.is_active is False

    async def test_deleted_user_cannot_login(self, client: AsyncClient, test_user, auth_headers, db):
        await client.delete("/api/v1/auth/account", headers=auth_headers)

        resp = await client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "Password123!",
        })
        assert resp.status_code == 401

    async def test_unauthenticated_delete(self, client: AsyncClient):
        resp = await client.delete("/api/v1/auth/account")
        assert resp.status_code in (401, 403)
