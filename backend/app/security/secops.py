"""Structured security event logging (SECOPS).

Security-relevant activity is emitted as structured events with a stable
event type, severity and correlation id. Only operational metadata is
recorded - never credentials, prompts or chain-of-thought.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.security.redaction import mask_identifier, redact_value

logger = logging.getLogger("security.events")


class EventType:
    AUTH_FAILURE = "AUTH_FAILURE"
    AUTH_SUCCESS = "AUTH_SUCCESS"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROMPT_INJECTION_BLOCKED = "PROMPT_INJECTION_BLOCKED"
    JAILBREAK_BLOCKED = "JAILBREAK_BLOCKED"
    PROMPT_LEAKAGE_BLOCKED = "PROMPT_LEAKAGE_BLOCKED"
    UNAUTHORIZED_RESOURCE_ACCESS = "UNAUTHORIZED_RESOURCE_ACCESS"
    CONVERSATION_ACCESS_DENIED = "CONVERSATION_ACCESS_DENIED"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    SUSPICIOUS_REQUEST = "SUSPICIOUS_REQUEST"
    AGENT_FAILURE = "AGENT_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    LLM_FAILURE = "LLM_FAILURE"
    REQUEST_COMPLETED = "REQUEST_COMPLETED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"


class Severity:
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    ORDER = {INFO: 0, WARNING: 1, HIGH: 2, CRITICAL: 3}


# Correlation id for the current request, set by the request-id middleware.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def current_request_id() -> str:
    return request_id_var.get() or ""


def new_request_id() -> str:
    import uuid

    return str(uuid.uuid4())


@dataclass
class SecurityEvent:
    event_type: str
    severity: str = Severity.INFO
    request_id: str = ""
    customer_id: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    error_code: Optional[str] = None
    client_ip: Optional[str] = None
    duration_ms: Optional[int] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": self.request_id or current_request_id(),
            "event_type": self.event_type,
            "severity": self.severity,
            "customer_id": mask_identifier(self.customer_id) if self.customer_id else None,
            "endpoint": self.endpoint,
            "method": self.method,
            "status_code": self.status_code,
            "error_code": self.error_code,
            "client_ip": self.client_ip,
            "duration_ms": self.duration_ms,
            "detail": redact_value(self.detail or {}),
        }


class SecurityLogger:
    """Emits structured security events to the application log."""

    def emit(
        self,
        event_type: str,
        severity: str = Severity.INFO,
        request_id: str = "",
        customer_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        method: Optional[str] = None,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        client_ip: Optional[str] = None,
        duration_ms: Optional[int] = None,
        **detail: Any,
    ) -> SecurityEvent:
        event = SecurityEvent(
            event_type=event_type,
            severity=severity,
            request_id=request_id or current_request_id(),
            customer_id=customer_id,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            error_code=error_code,
            client_ip=client_ip,
            duration_ms=duration_ms,
            detail=detail,
        )
        payload = event.to_dict()
        line = json.dumps(payload, default=str)
        level = {
            Severity.INFO: logging.INFO,
            Severity.WARNING: logging.WARNING,
            Severity.HIGH: logging.ERROR,
            Severity.CRITICAL: logging.CRITICAL,
        }.get(severity, logging.INFO)
        logger.log(level, line)
        return event

    # Convenience wrappers keep call sites short and consistent.
    def auth_failure(self, reason: str, **kwargs: Any) -> SecurityEvent:
        return self.emit(
            EventType.AUTH_FAILURE, Severity.WARNING, error_code=reason, **kwargs
        )

    def rate_limited(self, **kwargs: Any) -> SecurityEvent:
        return self.emit(
            EventType.RATE_LIMIT_EXCEEDED, Severity.WARNING, **kwargs
        )

    def invalid_request(self, reason: str, **kwargs: Any) -> SecurityEvent:
        return self.emit(
            EventType.INVALID_REQUEST, Severity.WARNING, error_code=reason, **kwargs
        )

    def blocked_attack(self, threat: str, reason: str, **kwargs: Any) -> SecurityEvent:
        mapping = {
            "prompt_injection": EventType.PROMPT_INJECTION_BLOCKED,
            "jailbreak": EventType.JAILBREAK_BLOCKED,
            "prompt_leakage": EventType.PROMPT_LEAKAGE_BLOCKED,
            "sensitive_info_request": EventType.PROMPT_LEAKAGE_BLOCKED,
        }
        severity = Severity.HIGH if threat == "jailbreak" else Severity.WARNING
        return self.emit(
            mapping.get(threat, EventType.SUSPICIOUS_REQUEST),
            severity,
            error_code=reason,
            **kwargs,
        )


security_logger = SecurityLogger()


def configure_security_logging(level: str = "INFO") -> None:
    """Send structured security events to stdout as single-line JSON."""
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "_secops", False)
        for h in logger.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._secops = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False