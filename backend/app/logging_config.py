"""
Structured logging configuration.
"""
import logging
import sys
from pathlib import Path

import structlog
from pythonjsonlogger import jsonlogger

from app.config import running_on_vercel, settings


def setup_logging():
    """Configure structured logging for the application."""

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO if settings.APP_ENV == "production" else logging.DEBUG,
    )

    root_logger = logging.getLogger()

    # File logging only when filesystem is writable (skip on Vercel)
    if not running_on_vercel():
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        json_handler = logging.FileHandler(log_dir / "app.log")
        json_handler.setFormatter(
            jsonlogger.JsonFormatter(
                "%(asctime)s %(name)s %(levelname)s %(message)s",
                timestamp=True,
            )
        )
        root_logger.addHandler(json_handler)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.APP_ENV != "production"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    return structlog.get_logger()


# Initialize logger
logger = setup_logging()
