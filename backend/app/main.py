"""FastAPI application entry point.

Run with:

    uvicorn app.main:app --reload

Security posture: authentication, authorisation, validation, guardrails,
tool permissions and verified database facts are all enforced in code. The LLM
is never a security authority - it can only rephrase facts the tools returned.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import SERVICE_VERSION, router
from app.security.auth import ALLOWED_ORIGINS
from app.security.middleware import register_security_middleware
from app.security.redaction import redact
from app.security.secops import (
    EventType,
    Severity,
    configure_security_logging,
    current_request_id,
    security_logger,
)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
DEBUG = os.getenv("DEBUG", "false").strip().lower() in {"1", "true", "yes"}
DOCS_ENABLED = os.getenv("DISABLE_DOCS", "false").strip().lower() not in {"1", "true", "yes"}

configure_security_logging(os.getenv("SECURITY_LOG_LEVEL", "INFO"))

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "AI customer support resolution agent. The agent validates input, applies "
    "security guardrails, selects authorised tools, verifies results against "
    "the database and either resolves the request or escalates it. "
    "Clients cannot select tools or internal fields."
)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or current_request_id()


def _safe_error(code: str, message: str, request: Request, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": {"code": code, "message": message, "request_id": _request_id(request)},
        },
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Customer Support Resolution Agent",
        version=SERVICE_VERSION,
        description=DESCRIPTION,
        debug=False,  # never expose stack traces in responses
        docs_url="/docs" if DOCS_ENABLED else None,
        redoc_url="/redoc" if DOCS_ENABLED else None,
        openapi_url="/openapi.json" if DOCS_ENABLED else None,
    )

    # Host header validation (only meaningful when a host list is configured).
    allowed_hosts = [
        host.strip()
        for host in os.getenv("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    # ------------------------------- security middleware (logical order)
    register_security_middleware(app)

    # ------------------------------------------------------- CORS
    # Added LAST on purpose. Starlette treats the most recently added
    # middleware as the outermost layer, so CORS must wrap the security
    # middleware. Otherwise a response short-circuited by the rate limiter or
    # the auth guard (401/403/413/429) never passes back through CORS and
    # carries no Access-Control-Allow-Origin, so a browser client cannot read
    # the response at all and reports an opaque network error instead.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,  # API-key auth must not ride on cookies
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID", "Accept"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )

    # ------------------------------------------------ safe error handling
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        request_id = _request_id(request)
        # Field names and reasons only - never echo submitted values back.
        fields = sorted({str(err.get("loc", ["body"])[-1]) for err in exc.errors()})
        security_logger.emit(
            EventType.INVALID_REQUEST,
            Severity.WARNING,
            request_id=request_id,
            endpoint=request.url.path,
            method=request.method,
            status_code=422,
            error_code="VALIDATION_ERROR",
            fields=fields,
        )
        return _safe_error(
            "VALIDATION_ERROR",
            "The request payload is invalid.",
            request,
            422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        request_id = _request_id(request)
        detail = exc.detail
        code, message = "HTTP_ERROR", "The request could not be completed."
        if isinstance(detail, dict):
            code = detail.get("error_code", code)
            message = detail.get("message", message)
        security_logger.emit(
            EventType.INVALID_REQUEST,
            Severity.INFO if exc.status_code < 500 else Severity.HIGH,
            request_id=request_id,
            endpoint=request.url.path,
            method=request.method,
            status_code=exc.status_code,
            error_code=code,
        )
        return _safe_error(code, message, request, exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        request_id = _request_id(request)
        # Full detail goes to the server log only, redacted; the client gets a
        # generic message with no traceback, path or SQL.
        logger.error(
            "unhandled_error request_id=%s path=%s error=%s",
            request_id,
            redact(request.url.path, limit=120),
            redact(f"{type(exc).__name__}: {exc}", limit=500),
        )
        security_logger.emit(
            EventType.AGENT_FAILURE,
            Severity.HIGH,
            request_id=request_id,
            endpoint=request.url.path,
            method=request.method,
            status_code=500,
            error_code="INTERNAL_ERROR",
            exception_type=type(exc).__name__,
        )
        return _safe_error(
            "INTERNAL_ERROR", "The request could not be completed.", request, 500
        )

    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "service": "ai-customer-support-resolution-agent",
            "version": SERVICE_VERSION,
            "docs": "/docs" if DOCS_ENABLED else None,
        }

    return app


app = create_app()