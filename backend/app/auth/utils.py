from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import secrets

import bcrypt
import httpx
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import running_on_vercel, settings
from app.database import get_db
from app.models import User

# 开发模式使用可选的 Bearer token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire, "type": "access"}, settings.SECRET_KEY)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": user_id, "exp": expire, "type": "refresh"}, settings.SECRET_KEY)


def _normalize_username(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")
    return cleaned[:64] or "user"


async def _fetch_easystarter_session_user(
    *,
    server_url: str,
    cookie: str,
) -> dict | None:
    base = server_url.rstrip("/")
    if not base:
        return None
    transport = httpx.AsyncHTTPTransport()
    async with httpx.AsyncClient(timeout=5, transport=transport, mounts={}) as client:
        resp = await client.get(
            f"{base}/api/auth/get-session",
            headers={"cookie": cookie},
        )
    if resp.status_code != 200:
        return None
    data = resp.json()
    user = data.get("user") if isinstance(data, dict) else None
    return user if isinstance(user, dict) else None


async def _get_or_create_user_from_easystarter(
    *,
    db: AsyncSession,
    session_user: dict,
) -> User | None:
    raw_email = session_user.get("email")
    if not raw_email or not isinstance(raw_email, str):
        return None
    email = raw_email.lower()

    existing = await db.execute(select(User).where(User.email == email))
    user = existing.scalar_one_or_none()
    if user is not None:
        return user

    user_id = session_user.get("id")
    if not user_id or not isinstance(user_id, str):
        user_id = secrets.token_hex(16)

    desired_username = session_user.get("username") or session_user.get("name") or email.split("@")[0]
    username = _normalize_username(str(desired_username))

    candidate = username
    if len(candidate) > 48:
        candidate = candidate[:48]
    candidate = f"{candidate}_{user_id[:12]}"
    candidate = candidate[:64]

    username_check = await db.execute(select(User).where(User.username == candidate))
    if username_check.scalar_one_or_none() is not None:
        candidate = f"user_{user_id[:12]}"

    user = User(
        id=user_id,
        username=candidate,
        email=email,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "INVALID_TOKEN", "message": "Could not validate credentials"}},
    )
    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            user_id: str | None = payload.get("sub")
            if user_id is None or payload.get("type") != "access":
                raise credentials_exception
        except (JWTError, HTTPException):
            payload = None

        if payload is not None:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user is None:
                raise credentials_exception
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": "USER_INACTIVE", "message": "User account is inactive"}},
                )
            return user

    cookie = request.headers.get("cookie")
    if cookie and settings.EASYSTARTER_SERVER_URL:
        session_user = await _fetch_easystarter_session_user(
            server_url=settings.EASYSTARTER_SERVER_URL,
            cookie=cookie,
        )
        if session_user is not None:
            bridged = await _get_or_create_user_from_easystarter(db=db, session_user=session_user)
            if bridged is not None:
                return bridged

    # Dev mode: auto-create and return a dev user so the app works without login
    # Guard: only allow on truly local requests to prevent accidental production bypass
    if settings.APP_ENV == "development" and not running_on_vercel():
        return await _get_or_create_dev_user(db)

    raise credentials_exception


async def _get_or_create_dev_user(db: AsyncSession) -> User:
    """Return a persistent dev user for local development."""
    dev_email = "dev@localhost.dev"
    result = await db.execute(select(User).where(User.email == dev_email))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        id="dev-local-user",
        username="dev",
        email=dev_email,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
