from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import running_on_vercel, settings
from app.database import get_db
from app.models import ApiToken, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ── Personal Access Tokens ────────────────────────────────────────────────

API_TOKEN_PREFIX = "atl_"
VALID_SCOPES = {"read", "write", "admin"}
# Admin implicitly grants write+read; write implicitly grants read.
_SCOPE_IMPLIES = {
    "admin": {"admin", "write", "read"},
    "write": {"write", "read"},
    "read": {"read"},
}


def generate_api_token() -> tuple[str, str, str]:
    """Return (plaintext_token, token_prefix, token_hash). Plaintext is shown once."""
    # 32 bytes -> 52 base32 chars. Uppercase-only; strip padding.
    body = secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:40]
    plaintext = f"{API_TOKEN_PREFIX}{body}"
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    # Show enough prefix to recognise the token in listings without leaking it.
    token_prefix = plaintext[: len(API_TOKEN_PREFIX) + 8]
    return plaintext, token_prefix, token_hash


def hash_api_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def normalize_scopes(scopes: str | list[str] | None) -> str:
    """Validate + dedupe + canonical-order scope string."""
    if not scopes:
        return "read"
    raw = scopes.split() if isinstance(scopes, str) else list(scopes)
    seen: list[str] = []
    for s in raw:
        s = s.strip().lower()
        if not s:
            continue
        if s not in VALID_SCOPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_SCOPE", "message": f"Unknown scope: {s}"}},
            )
        if s not in seen:
            seen.append(s)
    if not seen:
        return "read"
    # canonical order: admin, write, read
    order = ["admin", "write", "read"]
    return " ".join(s for s in order if s in seen)


def scope_satisfies(granted: str, required: str) -> bool:
    """Does a space-separated `granted` scope string satisfy `required`?"""
    required = required.strip().lower()
    for s in granted.split():
        if required in _SCOPE_IMPLIES.get(s.strip().lower(), set()):
            return True
    return False


# ── Password / JWT helpers ────────────────────────────────────────────────


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


# ── Current user resolution (JWT session OR PAT) ──────────────────────────


async def _resolve_pat(token: str, db: AsyncSession, request: Request) -> Optional[User]:
    """Validate a PAT and return the owning user, or None if invalid.

    Also enforces scope by HTTP method at resolution time:
      - GET/HEAD/OPTIONS require `read`
      - POST/PUT/PATCH/DELETE require `write`
      - `admin` is never required implicitly (reserved for future admin-only
        endpoints that call `require_scope('admin')` explicitly)
    """
    token_hash = hash_api_token(token)
    result = await db.execute(select(ApiToken).where(ApiToken.token_hash == token_hash))
    api_token = result.scalar_one_or_none()
    if api_token is None:
        return None
    if api_token.revoked_at is not None:
        return None
    if api_token.expires_at is not None:
        exp = api_token.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc):
            return None

    # Scope check based on request method. Enforcing here covers all existing
    # routes without per-endpoint changes.
    method = (request.method or "GET").upper()
    required = "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read"
    if not scope_satisfies(api_token.scopes or "", required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "INSUFFICIENT_SCOPE",
                    "message": f"Token is missing required scope: {required}",
                }
            },
        )

    result = await db.execute(select(User).where(User.id == api_token.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or user.deleted_at is not None:
        return None

    # Expose auth context to middleware/routes without another DB round-trip.
    request.state.auth_kind = "pat"
    request.state.api_token_id = api_token.id
    request.state.auth_scopes = api_token.scopes or "read"

    # Fire-and-forget last_used_at bump. Use a separate UPDATE so we don't taint
    # the request's transaction if the caller rolls back.
    try:
        from app.models import ApiToken as _AT
        await db.execute(
            _AT.__table__.update()
            .where(_AT.__table__.c.id == api_token.id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await db.commit()
    except Exception:
        pass

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
        # Branch on token shape: PATs are plaintext `atl_*`; JWTs are three
        # dot-separated base64 segments. This avoids an unnecessary DB lookup
        # for every JWT-authenticated request.
        if token.startswith(API_TOKEN_PREFIX):
            user = await _resolve_pat(token, db, request)
            if user is not None:
                return user
            raise credentials_exception

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
            if user.deleted_at:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error": {"code": "ACCOUNT_DELETED", "message": "Account has been deleted"}},
                )
            # Interactive session: full privileges, but we still expose context.
            request.state.auth_kind = "session"
            request.state.api_token_id = None
            request.state.auth_scopes = "admin"
            return user

    # Dev mode: auto-create and return a dev user so the app works without login.
    # Requires explicit DEV_AUTO_LOGIN=true AND non-production environment.
    if settings.DEV_AUTO_LOGIN and settings.APP_ENV != "production" and not running_on_vercel():
        user = await _get_or_create_dev_user(db)
        request.state.auth_kind = "dev"
        request.state.api_token_id = None
        request.state.auth_scopes = "admin"
        return user

    raise credentials_exception


def require_scope(required: str):
    """Dependency factory: require that the current auth context grants `required`.

    JWT sessions and dev-mode are treated as `admin` (full access). PATs must
    explicitly carry a scope that implies `required`.
    """
    async def _dep(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> User:
        granted = getattr(request.state, "auth_scopes", "admin")
        if not scope_satisfies(granted, required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "INSUFFICIENT_SCOPE",
                        "message": f"Token is missing required scope: {required}",
                    }
                },
            )
        return user

    return _dep


async def require_session(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Routes that must NOT be callable via a PAT (e.g. managing PATs themselves)."""
    if getattr(request.state, "auth_kind", "session") == "pat":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "SESSION_REQUIRED",
                    "message": "This endpoint requires an interactive session (not a PAT).",
                }
            },
        )
    return user


async def _get_or_create_dev_user(db: AsyncSession) -> User:
    """Return a persistent dev user for local development."""
    # Prefer the real seeded dev account if it exists
    result = await db.execute(select(User).where(User.email == "dev@test.local"))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

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

