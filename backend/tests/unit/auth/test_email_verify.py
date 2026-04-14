"""Tests for email verification and password reset flows."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailVerification, User


pytestmark = pytest.mark.asyncio


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TestVerifyEmail:
    """POST /api/v1/auth/verify-email"""

    async def _create_verification(self, db: AsyncSession, user: User, code: str = "ABC123"):
        """Helper to insert an email verification record."""
        v = EmailVerification(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=_hash_token(f"{code}:{user.id}"),
            purpose="verify_email",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        db.add(v)
        await db.commit()
        return v

    async def test_verify_success(self, client: AsyncClient, test_user, db):
        test_user.email_verified = False
        await db.commit()
        await self._create_verification(db, test_user, "ABC123")

        resp = await client.post("/api/v1/auth/verify-email", json={
            "email": test_user.email,
            "code": "ABC123",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        await db.refresh(test_user)
        assert test_user.email_verified is True

    async def test_verify_wrong_code(self, client: AsyncClient, test_user, db):
        test_user.email_verified = False
        await db.commit()
        await self._create_verification(db, test_user, "ABC123")

        resp = await client.post("/api/v1/auth/verify-email", json={
            "email": test_user.email,
            "code": "WRONG1",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_CODE"

    async def test_verify_expired_code(self, client: AsyncClient, test_user, db):
        test_user.email_verified = False
        await db.commit()

        v = EmailVerification(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            token_hash=_hash_token(f"EXP123:{test_user.id}"),
            purpose="verify_email",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),  # expired
        )
        db.add(v)
        await db.commit()

        resp = await client.post("/api/v1/auth/verify-email", json={
            "email": test_user.email,
            "code": "EXP123",
        })
        assert resp.status_code == 400

    async def test_verify_already_used(self, client: AsyncClient, test_user, db):
        test_user.email_verified = False
        await db.commit()

        v = EmailVerification(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            token_hash=_hash_token(f"USED12:{test_user.id}"),
            purpose="verify_email",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            used_at=datetime.now(timezone.utc),  # already used
        )
        db.add(v)
        await db.commit()

        resp = await client.post("/api/v1/auth/verify-email", json={
            "email": test_user.email,
            "code": "USED12",
        })
        assert resp.status_code == 400

    async def test_verify_case_insensitive_code(self, client: AsyncClient, test_user, db):
        test_user.email_verified = False
        await db.commit()
        await self._create_verification(db, test_user, "ABC123")

        resp = await client.post("/api/v1/auth/verify-email", json={
            "email": test_user.email,
            "code": "abc123",  # lowercase
        })
        assert resp.status_code == 200

    async def test_verify_nonexistent_email(self, client: AsyncClient):
        resp = await client.post("/api/v1/auth/verify-email", json={
            "email": "nobody@example.com",
            "code": "ABC123",
        })
        assert resp.status_code == 400


class TestResendVerification:
    """POST /api/v1/auth/resend-verification"""

    async def test_resend_success(self, client: AsyncClient, test_user, db, mock_email):
        test_user.email_verified = False
        await db.commit()

        resp = await client.post("/api/v1/auth/resend-verification", json={
            "email": test_user.email,
        })
        assert resp.status_code == 200
        mock_email["send"].assert_awaited_once()

    async def test_resend_already_verified(self, client: AsyncClient, test_user, mock_email):
        """Should return 200 but NOT send email (don't reveal status)."""
        resp = await client.post("/api/v1/auth/resend-verification", json={
            "email": test_user.email,
        })
        assert resp.status_code == 200
        mock_email["send"].assert_not_awaited()

    async def test_resend_nonexistent_email(self, client: AsyncClient, mock_email):
        """Should return 200 without revealing email doesn't exist."""
        resp = await client.post("/api/v1/auth/resend-verification", json={
            "email": "nobody@example.com",
        })
        assert resp.status_code == 200
        mock_email["send"].assert_not_awaited()


class TestPasswordReset:
    """POST /api/v1/auth/request-password-reset + /reset-password"""

    async def _create_reset_code(self, db: AsyncSession, user: User, code: str = "RST789"):
        v = EmailVerification(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=_hash_token(f"{code}:{user.id}"),
            purpose="reset_password",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(v)
        await db.commit()
        return v

    async def test_request_reset_success(self, client: AsyncClient, test_user, mock_email):
        resp = await client.post("/api/v1/auth/request-password-reset", json={
            "email": test_user.email,
        })
        assert resp.status_code == 200
        mock_email["send"].assert_awaited_once()

    async def test_request_reset_nonexistent_email(self, client: AsyncClient, mock_email):
        """Should return 200 without revealing email doesn't exist."""
        resp = await client.post("/api/v1/auth/request-password-reset", json={
            "email": "nobody@example.com",
        })
        assert resp.status_code == 200
        mock_email["send"].assert_not_awaited()

    async def test_reset_password_success(self, client: AsyncClient, test_user, db):
        await self._create_reset_code(db, test_user, "RST789")

        resp = await client.post("/api/v1/auth/reset-password", json={
            "email": test_user.email,
            "code": "RST789",
            "new_password": "NewPassword999!",
        })
        assert resp.status_code == 200

        # Verify new password works
        resp = await client.post("/api/v1/auth/login", json={
            "email": test_user.email,
            "password": "NewPassword999!",
        })
        assert resp.status_code == 200

    async def test_reset_password_wrong_code(self, client: AsyncClient, test_user, db):
        await self._create_reset_code(db, test_user, "RST789")

        resp = await client.post("/api/v1/auth/reset-password", json={
            "email": test_user.email,
            "code": "WRONG1",
            "new_password": "NewPassword999!",
        })
        assert resp.status_code == 400

    async def test_reset_password_weak_password(self, client: AsyncClient, test_user, db):
        await self._create_reset_code(db, test_user, "RST789")

        resp = await client.post("/api/v1/auth/reset-password", json={
            "email": test_user.email,
            "code": "RST789",
            "new_password": "short",
        })
        assert resp.status_code == 422  # Pydantic validation
