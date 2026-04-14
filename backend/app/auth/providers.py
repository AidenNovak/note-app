"""OAuth provider verification for Apple, Google, GitHub."""
from __future__ import annotations

import time
from typing import Optional

import httpx
from jose import jwt as jose_jwt, jwk, JWTError

# ── Apple Sign In ─────────────────────────────────────────

_apple_jwks_cache: dict | None = None
_apple_jwks_fetched_at: float = 0
_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_ISSUER = "https://appleid.apple.com"
_APPLE_JWKS_TTL = 3600  # 1 hour


async def _get_apple_jwks() -> dict:
    global _apple_jwks_cache, _apple_jwks_fetched_at
    now = time.time()
    if _apple_jwks_cache and (now - _apple_jwks_fetched_at) < _APPLE_JWKS_TTL:
        return _apple_jwks_cache
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_APPLE_JWKS_URL)
        resp.raise_for_status()
        _apple_jwks_cache = resp.json()
        _apple_jwks_fetched_at = now
        return _apple_jwks_cache


def _find_apple_key(jwks: dict, kid: str) -> dict | None:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def verify_apple_identity_token(
    identity_token: str,
    bundle_id: str,
) -> dict:
    """Verify an Apple identity token and return the decoded claims.

    Returns dict with keys: sub, email, email_verified, name (optional).
    Raises ValueError on invalid token.
    """
    try:
        unverified_header = jose_jwt.get_unverified_header(identity_token)
    except JWTError as e:
        raise ValueError(f"Invalid token header: {e}")

    kid = unverified_header.get("kid")
    if not kid:
        raise ValueError("Token missing kid header")

    jwks = await _get_apple_jwks()
    key_data = _find_apple_key(jwks, kid)
    if not key_data:
        # Refresh JWKS in case Apple rotated keys
        global _apple_jwks_cache
        _apple_jwks_cache = None
        jwks = await _get_apple_jwks()
        key_data = _find_apple_key(jwks, kid)
        if not key_data:
            raise ValueError(f"No matching key found for kid={kid}")

    try:
        public_key = jwk.construct(key_data, algorithm="RS256")
        claims = jose_jwt.decode(
            identity_token,
            public_key,
            algorithms=["RS256"],
            audience=bundle_id,
            issuer=_APPLE_ISSUER,
        )
    except JWTError as e:
        raise ValueError(f"Token verification failed: {e}")

    if not claims.get("sub"):
        raise ValueError("Token missing sub claim")

    return {
        "sub": claims["sub"],
        "email": claims.get("email"),
        "email_verified": claims.get("email_verified", False),
    }


# ── Google OAuth ──────────────────────────────────────────

_GOOGLE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUER = "https://accounts.google.com"

_google_jwks_cache: dict | None = None
_google_jwks_fetched_at: float = 0
_GOOGLE_JWKS_TTL = 3600


async def _get_google_jwks() -> dict:
    global _google_jwks_cache, _google_jwks_fetched_at
    now = time.time()
    if _google_jwks_cache and (now - _google_jwks_fetched_at) < _GOOGLE_JWKS_TTL:
        return _google_jwks_cache
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_GOOGLE_CERTS_URL)
        resp.raise_for_status()
        _google_jwks_cache = resp.json()
        _google_jwks_fetched_at = now
        return _google_jwks_cache


async def verify_google_id_token(
    id_token: str,
    client_id: str,
) -> dict:
    """Verify a Google ID token and return decoded claims.

    Returns dict with keys: sub, email, email_verified, name, picture.
    Raises ValueError on invalid token.
    """
    try:
        unverified_header = jose_jwt.get_unverified_header(id_token)
    except JWTError as e:
        raise ValueError(f"Invalid token header: {e}")

    kid = unverified_header.get("kid")
    if not kid:
        raise ValueError("Token missing kid header")

    jwks = await _get_google_jwks()
    key_data = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            key_data = key
            break

    if not key_data:
        global _google_jwks_cache
        _google_jwks_cache = None
        jwks = await _get_google_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                key_data = key
                break

    if not key_data:
        raise ValueError(f"No matching key found for kid={kid}")

    try:
        public_key = jwk.construct(key_data, algorithm="RS256")
        claims = jose_jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=_GOOGLE_ISSUER,
        )
    except JWTError as e:
        raise ValueError(f"Token verification failed: {e}")

    return {
        "sub": claims["sub"],
        "email": claims.get("email"),
        "email_verified": claims.get("email_verified", False),
        "name": claims.get("name"),
        "picture": claims.get("picture"),
    }


# ── GitHub OAuth ──────────────────────────────────────────

_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"

# ── Google OAuth Code Exchange ────────────────────────────

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


async def exchange_google_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange a Google OAuth authorization code for user info.

    Returns dict with keys: sub, email, email_verified, name, picture.
    Raises ValueError on failure.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError(
                f"Google token exchange failed: {token_data.get('error_description', token_data.get('error', 'unknown'))}"
            )

        userinfo_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo_resp.raise_for_status()
        info = userinfo_resp.json()

        return {
            "sub": info["sub"],
            "email": info.get("email"),
            "email_verified": info.get("email_verified", False),
            "name": info.get("name"),
            "picture": info.get("picture"),
        }


async def exchange_github_code(
    code: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Exchange a GitHub OAuth code for user info.

    Returns dict with keys: sub, email, email_verified, name, avatar_url, login.
    Raises ValueError on failure.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        # Exchange code for access token
        token_resp = await client.post(
            _GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError(f"GitHub token exchange failed: {token_data.get('error_description', 'unknown')}")

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        # Get user profile
        user_resp = await client.get(_GITHUB_USER_URL, headers=headers)
        user_resp.raise_for_status()
        user = user_resp.json()

        # Get verified email
        email = user.get("email")
        email_verified = False
        if not email:
            emails_resp = await client.get(_GITHUB_EMAILS_URL, headers=headers)
            if emails_resp.status_code == 200:
                for em in emails_resp.json():
                    if em.get("primary") and em.get("verified"):
                        email = em["email"]
                        email_verified = True
                        break
        else:
            email_verified = True

        return {
            "sub": str(user["id"]),
            "email": email,
            "email_verified": email_verified,
            "name": user.get("name"),
            "avatar_url": user.get("avatar_url"),
            "login": user.get("login"),
            "access_token": access_token,
        }
