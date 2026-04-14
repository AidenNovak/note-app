from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import running_on_vercel, settings
from app.database import init_db, ping_db
from app.logging_config import logger
from app.middleware import (
    SecurityHeadersMiddleware,
    RequestLoggingMiddleware,
    setup_rate_limiter,
)
from api.v1.auth import router as auth_router
from api.v1.notes import router as notes_router
from api.v1.files import router as files_router
from api.v1.tasks import router as tasks_create_router
from api.v1.tasks import task_router
from api.v1.folders import router as folders_router
from api.v1.tags import router as tags_router
from api.v1.search import router as search_router
from api.v1.versions import router as versions_router
from api.v1.mind import router as mind_router
from api.v1.insights import router as insights_router
from api.v1.ground import router as ground_router
from api.v1.payments import router as payments_router, webhook_router
from api.v1.storage import router as storage_router

_LANDING_HTML = Path(__file__).with_name("landing.html")
_PRODUCT_HTML = Path(__file__).with_name("product.html")
_API_DOCS_HTML = Path(__file__).with_name("api_docs.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("application_startup", env=settings.APP_ENV, version=settings.APP_VERSION)
    if os.getenv("APP_ENV") == "test" or settings.APP_ENV == "test" or running_on_vercel():
        try:
            await init_db()
            logger.info("database_initialized")
        except Exception:
            logger.exception("database_initialization_failed")
    yield
    logger.info("application_shutdown")


app = FastAPI(
    title=settings.APP_NAME,
    description="Your Second Digital Mind — multi-modal note capture + AI knowledge graph",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.APP_ENV != "production" else None,
    redoc_url="/api/redoc" if settings.APP_ENV != "production" else None,
)

limiter = setup_rate_limiter(app)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app" if settings.APP_ENV != "production" else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

app.include_router(auth_router, prefix="/api/v1")
app.include_router(notes_router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1")
app.include_router(tasks_create_router, prefix="/api/v1")
app.include_router(task_router, prefix="/api/v1")
app.include_router(folders_router, prefix="/api/v1")
app.include_router(tags_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
app.include_router(versions_router, prefix="/api/v1")
app.include_router(mind_router, prefix="/api/v1")
app.include_router(insights_router, prefix="/api/v1")
app.include_router(ground_router, prefix="/api/v1")
app.include_router(payments_router, prefix="/api/v1")
app.include_router(storage_router, prefix="/api/v1")
app.include_router(webhook_router, prefix="/api")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred" if settings.APP_ENV == "production" else str(exc),
            }
        },
    )


@app.get("/")
async def root():
    return {"name": settings.APP_NAME, "version": settings.APP_VERSION, "environment": settings.APP_ENV}


@app.get("/api", response_class=HTMLResponse)
async def api_documentation():
    return HTMLResponse(_API_DOCS_HTML.read_text(encoding="utf-8"))


@app.get("/web", response_class=HTMLResponse)
async def web():
    return HTMLResponse(_LANDING_HTML.read_text(encoding="utf-8"))


@app.get("/product", response_class=HTMLResponse)
async def product():
    return HTMLResponse(_PRODUCT_HTML.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {"status": "ok", "name": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/ready")
async def ready():
    try:
        await ping_db()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "DATABASE_UNAVAILABLE", "message": str(exc)}},
        ) from exc
    return {"status": "ready", "database": "ok"}
