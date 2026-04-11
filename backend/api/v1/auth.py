import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut
from app.auth.utils import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

def _rate_limit_key(request: Request) -> str:
    if settings.APP_ENV == "test":
        return request.headers.get("x-test-key") or get_remote_address(request)
    return get_remote_address(request)

limiter = Limiter(key_func=_rate_limit_key)

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")  # Strict rate limit for registration
async def register(request: Request, body: RegisterRequest = Body(...), db: AsyncSession = Depends(get_db)):
    normalized_email = body.email.lower()
    normalized_username = body.username.strip()

    # check duplicate
    existing = await db.execute(
        select(User).where((User.email == normalized_email) | (User.username == normalized_username))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail={"error": {"code": "DUPLICATE", "message": "Email or username already exists"}})

    user = User(
        id=str(uuid.uuid4()),
        username=normalized_username,
        email=normalized_email,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")  # Prevent brute force attacks
async def login(request: Request, body: LoginRequest = Body(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail={"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid email or password"}})

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(token_body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from jose import JWTError, jwt
    from app.config import settings

    try:
        payload = jwt.decode(token_body.refresh_token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "refresh":
            raise JWTError()
        user_id = payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail={"error": {"code": "INVALID_TOKEN", "message": "Invalid refresh token"}})

    result = await db.execute(select(User).where(User.id == user_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=401, detail={"error": {"code": "USER_NOT_FOUND", "message": "User not found"}})

    return TokenResponse(
        access_token=create_access_token(user_id),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
async def update_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    body = await request.json()
    if "avatar_url" in body:
        current_user.avatar_url = body["avatar_url"]
    if "username" in body:
        current_user.username = body["username"]
    await db.commit()
    await db.refresh(current_user)
    return current_user
