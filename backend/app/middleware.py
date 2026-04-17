"""
Security middleware for rate limiting, logging, and security headers.
"""
from __future__ import annotations

import time
import logging
from typing import Callable

from fastapi import Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


def _rate_limit_key(request: Request) -> str:
    """Prefer token_id > IP so that PATs get their own bucket and can't be
    starved by an attacker sharing their egress IP."""
    state = getattr(request, "state", None)
    token_id = getattr(state, "api_token_id", None) if state else None
    if token_id:
        return f"tok:{token_id}"
    return get_remote_address(request)


# Configure rate limiter — uses RATE_LIMIT_STORAGE_URI from settings.
# Default "memory://" works for single-instance; set to "redis://..." for multi-worker.
limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=["100/minute"],
    storage_uri=settings.RATE_LIMIT_STORAGE_URI,
)

# Configure structured logging
logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # HSTS (only in production with HTTPS)
        if settings.APP_ENV == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # Content Security Policy
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'"
        )

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests with timing and status information."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()

        # Extract request info
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # Log successful requests
            logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration * 1000, 2),
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                },
            )

            # Add timing header
            response.headers["X-Process-Time"] = str(round(duration * 1000, 2))

            return response

        except Exception as exc:
            duration = time.time() - start_time

            # Log failed requests
            logger.error(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration * 1000, 2),
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                    "error": str(exc),
                },
                exc_info=True,
            )

            raise


def setup_rate_limiter(app):
    """Configure rate limiter for the application."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return limiter
