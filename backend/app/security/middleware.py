"""Ordered security middleware for the API layer.

Starlette executes middleware in reverse registration order, so
``register_security_middleware`` adds them last-first to produce the logical
pipeline below:

    request id -> security headers -> request size -> CORS ->
    content type -> auth-failure tracking -> rate limiting -> route

Authentication itself stays a FastAPI dependency (see app/security/auth.py) so
existing agent/API code can keep using ``verify_api_key``.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.security.secops import (
    EventType,
    Severity,
    current_request_id,
    new_request_id,
    security_logger,
)

MAX_REQUEST_BYTES = 16 * 1024  # 16 KB is ample for a chat message
MAX_HEADER_VALUE_LENGTH = 512
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# Endpoints that never require authentication or rate limiting.
PUBLIC_PATHS = frozenset({"/health", "/api/health", "/docs", "/openapi.json", "/redoc"})

# Per-path request budgets: (max_requests, window_seconds)
PATH_LIMITS: Dict[str, Tuple[int, int]] = {
    "/api/v1/chat": (20, 60),
}
DEFAULT_CHAT_LIMIT = (20, 60)

# Repeated authentication failures per client before throttling.
AUTH_FAILURE_LIMIT = 10
AUTH_FAILURE_WINDOW = 300

_counters: Dict[str, Tuple[int, float]] = {}
_auth_failures: Dict[str, Tuple[int, float]] = {}
_last_sweep = time.time()


def _sweep(now: float) -> None:
    """Drop expired counters so memory cannot be used as a DoS vector."""
    global _last_sweep
    if now - _last_sweep < 60:
        return
    _last_sweep = now
    for store in (_counters, _auth_failures):
        for key in [k for k, (_, start) in store.items() if now - start > 3600]:
            store.pop(key, None)


def _hit(store: Dict[str, Tuple[int, float]], key: str, limit: int, window: int):
    """Return (allowed, count, retry_after)."""
    now = time.time()
    count, start = store.get(key, (0, now))
    if now - start > window:
        count, start = 0, now
    count += 1
    store[key] = (count, start)
    if count > limit:
        retry_after = max(1, int(window - (now - start)))
        return False, count, retry_after
    return True, count, 0


def _client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


def _error(code: str, message: str, status: int, request_id: str, **extra):
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": {"code": code, "message": message, "request_id": request_id, **extra},
        },
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a trusted correlation id to every request.

    A client-supplied X-Request-ID is only honoured when it looks benign;
    otherwise the server generates its own UUID. User input is never used as a
    security identifier.
    """

    async def dispatch(self, request: Request, call_next):
        _sweep(time.time())
        supplied = request.headers.get("x-request-id", "")
        if supplied and SAFE_REQUEST_ID.match(supplied) and len(supplied) <= MAX_HEADER_VALUE_LENGTH:
            request_id = supplied
        else:
            request_id = new_request_id()
            if supplied:
                security_logger.emit(
                    EventType.SUSPICIOUS_REQUEST,
                    Severity.INFO,
                    request_id=request_id,
                    endpoint=request.url.path,
                    method=request.method,
                    error_code="INVALID_REQUEST_ID_REPLACED",
                )
        request.state.request_id = request_id
        token = _set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            _reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response


def _set_request_id(value: str):
    from app.security.secops import request_id_var

    return request_id_var.set(value)


def _reset_request_id(token) -> None:
    from app.security.secops import request_id_var

    request_id_var.reset(token)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; sandbox"
    ),
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    # Honoured by browsers only over HTTPS. The app itself does not terminate
    # TLS, so a reverse proxy must provide it; without one HSTS is a no-op.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized bodies before they reach validation, tools or the LLM."""

    async def dispatch(self, request: Request, call_next):
        request_id = getattr(request.state, "request_id", "") or current_request_id()

        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > MAX_REQUEST_BYTES:
                    security_logger.emit(
                        EventType.PAYLOAD_TOO_LARGE,
                        Severity.WARNING,
                        request_id=request_id,
                        endpoint=request.url.path,
                        method=request.method,
                        error_code="REQUEST_TOO_LARGE",
                        client_ip=_client_ip(request),
                    )
                    return _error(
                        "REQUEST_TOO_LARGE",
                        "The request payload is too large.",
                        413,
                        request_id,
                    )
            except ValueError:
                return _error(
                    "INVALID_REQUEST", "The request could not be read.", 400, request_id
                )

        return await call_next(request)


class ContentTypeMiddleware(BaseHTTPMiddleware):
    """Only JSON is accepted where a request body is actually expected.

    Scoped to POST on non-public paths so unsupported methods still receive
    the router's normal 405 rather than a misleading 415.
    """

    ALLOWED = ("application/json",)

    async def dispatch(self, request: Request, call_next):
        request_id = getattr(request.state, "request_id", "") or current_request_id()
        if request.method == "POST" and request.url.path not in PUBLIC_PATHS:
            ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype not in self.ALLOWED:
                security_logger.emit(
                    EventType.UNSUPPORTED_MEDIA_TYPE,
                    Severity.WARNING,
                    request_id=request_id,
                    endpoint=request.url.path,
                    method=request.method,
                    error_code="UNSUPPORTED_MEDIA_TYPE",
                    client_ip=_client_ip(request),
                )
                return _error(
                    "UNSUPPORTED_MEDIA_TYPE",
                    "Content-Type must be application/json.",
                    415,
                    request_id,
                )
        return await call_next(request)


class AuthFailureMiddleware(BaseHTTPMiddleware):
    """Throttles repeated authentication failures (brute-force protection)."""

    async def dispatch(self, request: Request, call_next):
        request_id = getattr(request.state, "request_id", "") or current_request_id()
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        ip = _client_ip(request)
        now = time.time()
        count, start = _auth_failures.get(ip, (0, now))
        if now - start > AUTH_FAILURE_WINDOW:
            count, start = 0, now
        if count >= AUTH_FAILURE_LIMIT:
            security_logger.emit(
                EventType.AUTH_FAILURE,
                Severity.HIGH,
                request_id=request_id,
                endpoint=request.url.path,
                method=request.method,
                error_code="AUTH_FAILURE_THRESHOLD",
                client_ip=ip,
            )
            response = _error(
                "TOO_MANY_ATTEMPTS",
                "Too many failed attempts. Please try again later.",
                429,
                request_id,
                retry_after=AUTH_FAILURE_WINDOW,
            )
            # Match the request rate limiter: every 429 must tell the client when
            # to retry, otherwise it can only guess.
            response.headers["Retry-After"] = str(AUTH_FAILURE_WINDOW)
            return response

        response = await call_next(request)
        if response.status_code in {401, 403}:
            _auth_failures[ip] = (count + 1, start)
            security_logger.emit(
                EventType.AUTH_FAILURE,
                Severity.WARNING,
                request_id=request_id,
                endpoint=request.url.path,
                method=request.method,
                status_code=response.status_code,
                error_code="AUTH_REJECTED",
                client_ip=ip,
            )
        else:
            _auth_failures.pop(ip, None)
        return response


async def rate_limit_dispatch(request: Request, call_next, limits=None):
    """Per-endpoint request budget with a safe Retry-After header."""
    limits = limits or PATH_LIMITS
    request_id = getattr(request.state, "request_id", "") or current_request_id()
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    limit, window = limits.get(request.url.path, DEFAULT_CHAT_LIMIT)
    key = f"{_client_ip(request)}|{request.url.path}"
    allowed, count, retry_after = _hit(_counters, key, limit, window)

    if not allowed:
        security_logger.emit(
            EventType.RATE_LIMIT_EXCEEDED,
            Severity.WARNING,
            request_id=request_id,
            endpoint=request.url.path,
            method=request.method,
            error_code="RATE_LIMIT_EXCEEDED",
            client_ip=_client_ip(request),
            status_code=429,
        )
        response = _error(
            "RATE_LIMIT_EXCEEDED",
            "Too many requests. Please try again shortly.",
            429,
            request_id,
        )
        response.headers["Retry-After"] = str(retry_after)
        return response

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
    return response


async def security_headers_dispatch(request: Request, call_next):
    """Apply defensive response headers."""
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    try:
        del response.headers["server"]
    except KeyError:
        pass
    return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-endpoint request budget with a safe Retry-After header."""

    def __init__(self, app, limits: Dict[str, Tuple[int, int]] = None):
        super().__init__(app)
        self.limits = limits or PATH_LIMITS

    async def dispatch(self, request: Request, call_next):
        return await rate_limit_dispatch(request, call_next, self.limits)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defensive response headers. No secrets or internals are exposed."""

    async def dispatch(self, request: Request, call_next):
        return await security_headers_dispatch(request, call_next)


def register_security_middleware(
    app, auth_middleware_factory: Optional[Callable] = None
) -> None:
    """Register the stack. Added last-first so it executes in logical order.

    ``auth_middleware_factory`` is optional and normally omitted, because
    authentication is a route dependency (see app/security/auth.py). It is
    applied only when a caller supplies one, so the rate limiter below can never
    be registered twice - a duplicate registration charged every request two
    units of the budget and halved the effective limit.
    """
    if auth_middleware_factory is not None:
        app.add_middleware(auth_middleware_factory)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthFailureMiddleware)
    app.add_middleware(ContentTypeMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)