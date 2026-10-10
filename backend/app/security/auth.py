from fastapi import HTTPException, Depends
from fastapi.security import APIKeyHeader
import hmac
import os
from typing import List
import logging

from app.security.guardrails import guardrail_engine, GuardrailResult
from app.security.secops import (
    EventType,
    Severity,
    current_request_id,
    security_logger,
)
from app.security.redaction import register_secret

logger = logging.getLogger(__name__)

# API Key authentication
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

API_KEY = os.getenv("APP_API_KEY", "dev-api-key-change-in-production")
USING_DEFAULT_API_KEY = API_KEY == "dev-api-key-change-in-production"

# Guarantee the live credential is masked even if it appears in an unexpected
# place (exception text, third-party library output, debug dumps).
register_secret(API_KEY)

# The shipped default is a publicly known value. It is tolerated in
# development so the project runs out of the box, but a production deployment
# must never start with it - otherwise the service is protected by a credential
# that is published in this repository.
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()

if USING_DEFAULT_API_KEY:
    if ENVIRONMENT == "production":
        raise RuntimeError(
            "APP_API_KEY is still the default development value while "
            "ENVIRONMENT=production. Set a strong, unique APP_API_KEY before "
            "starting the service in production."
        )
    logger.warning(
        "APP_API_KEY is the default development value - do not use this "
        "configuration outside a local development machine."
    )


def load_allowed_origins() -> List[str]:
    """Explicit CORS allow-list from the environment.

    Wildcards are rejected: CORS is never treated as authentication.
    """
    raw = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
    )
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return [origin for origin in origins if origin != "*"]


ALLOWED_ORIGINS = load_allowed_origins()


def keys_match(candidate: str, expected: str) -> bool:
    """Constant-time API-key comparison."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(
        str(candidate).encode("utf-8"), str(expected).encode("utf-8")
    )


async def verify_api_key(api_key: str = Depends(api_key_header)):
    """Verify the API key for protected endpoints.

    Failures return a generic message: the expected key, its location and any
    authentication internals are never disclosed.
    """
    request_id = current_request_id()
    if not api_key:
        security_logger.emit(
            EventType.AUTH_FAILURE,
            Severity.WARNING,
            request_id=request_id,
            error_code="MISSING_API_KEY",
            detail={"reason": "header absent"},
        )
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error_code": "MISSING_API_KEY",
                "message": "Authentication is required for this endpoint.",
            },
        )
    if not keys_match(api_key, API_KEY):
        security_logger.emit(
            EventType.AUTH_FAILURE,
            Severity.WARNING,
            request_id=request_id,
            error_code="INVALID_API_KEY",
            detail={"reason": "credential mismatch"},
        )
        raise HTTPException(
            status_code=403,
            detail={
                "success": False,
                "error_code": "INVALID_API_KEY",
                "message": "Authentication failed.",
            },
        )
    return api_key


# The hardened middleware lives in app.security.middleware so the request-id,
# auth-failure and size guards share one bounded counter store. These names are
# re-exported here because earlier modules import them from this module.
from app.security.middleware import (  # noqa: E402,F401
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)


def validate_chat_input(request_data: dict) -> GuardrailResult:
    """Validate chat input through guardrails."""
    message = request_data.get("message", "")
    return guardrail_engine.check_input(message)