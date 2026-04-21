"""
Shared test fixtures for Truth Truth backend unit & integration tests.

Provides:
- Async SQLite database session (isolated per test)
- FastAPI TestClient with httpx
- User/auth helper fixtures
- External service mocks (email, storage, AI)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models import User
from app.auth.utils import create_access_token, create_refresh_token, hash_password


# ── Database Fixtures ─────────────────────────────────

@pytest_asyncio.fixture
async def db_engine():
    """Create a fresh in-memory SQLite engine for each test."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async session that rolls back after each test."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired to the FastAPI app with test DB override."""
    from app.main import app

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"x-test-key": str(uuid.uuid4())},  # unique key per test to avoid rate limits
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ── User Fixtures ─────────────────────────────────────

@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    """Create a basic test user with email/password."""
    user = User(
        id=str(uuid.uuid4()),
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("Password123!"),
        email_verified=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def second_user(db: AsyncSession) -> User:
    """Create a second test user for multi-user scenarios."""
    user = User(
        id=str(uuid.uuid4()),
        username="otheruser",
        email="other@example.com",
        hashed_password=hash_password("Password456!"),
        email_verified=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.fixture
def auth_headers(test_user: User) -> dict:
    """Valid Bearer auth headers for the test user."""
    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def second_auth_headers(second_user: User) -> dict:
    """Auth headers for the second user."""
    token = create_access_token(second_user.id)
    return {"Authorization": f"Bearer {token}"}


# ── Token Helpers ─────────────────────────────────────

@pytest.fixture
def make_tokens():
    """Factory to create access + refresh tokens for any user ID."""
    def _make(user_id: str) -> dict:
        return {
            "access_token": create_access_token(user_id),
            "refresh_token": create_refresh_token(user_id),
        }
    return _make


# ── External Service Mocks ────────────────────────────

@pytest.fixture
def mock_send_email(mocker) -> AsyncMock:
    """Mock the email sending service — prevents real emails in tests."""
    mock = mocker.patch(
        "api.v1.auth.send_email",
        new_callable=AsyncMock,
        return_value=True,
    )
    # Also patch at module level for any direct imports
    mocker.patch(
        "app.email.service.send_email",
        new_callable=AsyncMock,
        return_value=True,
    )
    return mock


@pytest.fixture
def mock_render_verification_email(mocker) -> MagicMock:
    """Mock email template rendering."""
    return mocker.patch(
        "api.v1.auth.render_verification_email",
        return_value=("Verify your email", "<p>Code: ABC123</p>"),
    )


@pytest.fixture
def mock_render_password_reset_email(mocker) -> MagicMock:
    """Mock password reset email template."""
    return mocker.patch(
        "api.v1.auth.render_password_reset_email",
        return_value=("Reset your password", "<p>Code: XYZ789</p>"),
    )


@pytest.fixture
def mock_email(mock_send_email, mock_render_verification_email, mock_render_password_reset_email):
    """Convenience: mock all email-related functions at once."""
    return {
        "send": mock_send_email,
        "render_verification": mock_render_verification_email,
        "render_reset": mock_render_password_reset_email,
    }


@pytest.fixture
def mock_stripe(mocker) -> MagicMock:
    """Mock Stripe HTTP requests via httpx."""
    mock = AsyncMock()
    mock.request.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"id": "cs_test_123", "url": "https://checkout.stripe.com/test"}),
        raise_for_status=MagicMock(),
    )
    mocker.patch("app.payments.service.httpx.AsyncClient", return_value=mock)
    return mock


@pytest.fixture
def mock_r2(mocker) -> MagicMock:
    """Mock R2/S3 storage client."""
    mock_client = MagicMock()
    mock_client.put_object = MagicMock(return_value={})
    mock_client.delete_object = MagicMock(return_value={})
    mock_client.generate_presigned_url = MagicMock(return_value="https://cdn.jilly.app/test-file")
    mocker.patch("app.storage.s3_client", mock_client)
    return mock_client


@pytest.fixture
def mock_openai(mocker) -> MagicMock:
    """Mock OpenAI embedding calls."""
    mock_resp = MagicMock()
    mock_resp.data = [MagicMock(embedding=[0.1] * 1536)]
    mocker.patch(
        "app.intelligence.embeddings.openai_client.embeddings.create",
        new_callable=AsyncMock,
        return_value=mock_resp,
    )
    return mock_resp
