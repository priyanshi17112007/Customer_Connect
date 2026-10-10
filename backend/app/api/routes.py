"""API routes.

Routes stay intentionally thin: validate, delegate to the agent, shape the
response. No agent logic, tool invocation or authorisation decisions live
here - the client can only ever submit a customer request.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from app.agents.support_agent import run_support_agent
from app.api.schemas import (
    ChatData,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthData,
    HealthResponse,
)
from app.security.auth import verify_api_key
from app.security.secops import (
    EventType,
    Severity,
    current_request_id,
    security_logger,
)

router = APIRouter()

SERVICE_VERSION = "1.0.0"

# Guardrail threat types that map onto security events.
THREAT_EVENTS = {
    "prompt_injection": EventType.PROMPT_INJECTION_BLOCKED,
    "jailbreak": EventType.JAILBREAK_BLOCKED,
    "prompt_leakage": EventType.PROMPT_LEAKAGE_BLOCKED,
    "sensitive_info_request": EventType.PROMPT_LEAKAGE_BLOCKED,
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or current_request_id()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description="Public liveness probe. Returns no configuration or secrets.",
)
def health(request: Request) -> HealthResponse:
    database_state = "connected"
    try:
        from app.database.session import engine

        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception:
        database_state = "unavailable"

    return HealthResponse(
        data=HealthData(
            status="ok" if database_state == "connected" else "degraded",
            version=SERVICE_VERSION,
            database=database_state,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
        request_id=_request_id(request),
    )


@router.post(
    "/api/v1/chat",
    response_model=ChatResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Submit a support request to the resolution agent",
    description=(
        "Accepts a customer request and returns the agent's verified answer. "
        "Tool usage is decided by the agent; clients cannot select tools, "
        "roles or any other internal field."
    ),
)
def chat(
    payload: ChatRequest,
    request: Request,
    _: str = Depends(verify_api_key),
) -> ChatResponse:
    started = time.perf_counter()
    request_id = _request_id(request)

    security_logger.emit(
        EventType.AUTH_SUCCESS,
        Severity.INFO,
        request_id=request_id,
        customer_id=payload.customer_id,
        endpoint="/api/v1/chat",
        method="POST",
    )

    outcome = run_support_agent(
        message=payload.message,
        customer_id=payload.customer_id,
        conversation_id=payload.conversation_id,
    )

    duration_ms = int((time.perf_counter() - started) * 1000)

    # Security events derived from the agent's own decisions, so the API never
    # re-implements guardrail or authorisation logic.
    if outcome.blocked:
        threat = outcome.result.threat_type or "suspicious_request"
        security_logger.emit(
            THREAT_EVENTS.get(threat, EventType.SUSPICIOUS_REQUEST),
            Severity.WARNING,
            request_id=request_id,
            customer_id=payload.customer_id,
            endpoint="/api/v1/chat",
            method="POST",
            error_code=outcome.error_code,
            duration_ms=duration_ms,
        )
    if outcome.error_code == "ORDER_NOT_ACCESSIBLE":
        security_logger.emit(
            EventType.UNAUTHORIZED_RESOURCE_ACCESS,
            Severity.HIGH,
            request_id=request_id,
            customer_id=payload.customer_id,
            endpoint="/api/v1/chat",
            method="POST",
            error_code="ORDER_NOT_ACCESSIBLE",
            duration_ms=duration_ms,
        )
    if outcome.error_code == "AGENT_FAILURE":
        security_logger.emit(
            EventType.AGENT_FAILURE,
            Severity.HIGH,
            request_id=request_id,
            customer_id=payload.customer_id,
            endpoint="/api/v1/chat",
            method="POST",
            error_code="AGENT_FAILURE",
            duration_ms=duration_ms,
        )

    security_logger.emit(
        EventType.REQUEST_COMPLETED,
        Severity.INFO,
        request_id=request_id,
        customer_id=payload.customer_id,
        endpoint="/api/v1/chat",
        method="POST",
        duration_ms=duration_ms,
        error_code=outcome.error_code,
        intent=outcome.intent,
        decision=outcome.decision.value if outcome.decision else None,
        resolved=outcome.resolved,
        escalated=outcome.escalated,
        tools=[call["tool"] for call in outcome.tool_calls],
    )

    tool_calls = [
        {"tool": call.get("tool"), "status": call.get("status"), "error_code": call.get("error_code")}
        for call in outcome.tool_calls
    ]

    return ChatResponse(
        data=ChatData(
            conversation_id=outcome.conversation_id,
            response=outcome.response,
            intent=outcome.intent,
            decision=outcome.decision.value if outcome.decision else None,
            resolved=outcome.resolved,
            escalated=outcome.escalated,
            ticket_id=outcome.ticket_id,
            error_code=outcome.error_code,
            activity=outcome.activity,
            tool_calls=tool_calls,
        ),
        request_id=request_id,
    )