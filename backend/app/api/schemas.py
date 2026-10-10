"""Request/response contracts for the public API.

These schemas are the trust boundary. They use ``extra="forbid"`` so a client
cannot smuggle internal fields (``role``, ``tool``, ``is_admin``,
``system_prompt`` ...) into the application: unknown fields are rejected before
any agent, tool or LLM work happens.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Identifier shapes accepted from clients.
CUSTOMER_ID_PATTERN = r"^CUS-\d{1,8}$"
CONVERSATION_ID_PATTERN = r"^CONV-\d{1,12}$"


class ChatRequest(BaseModel):
    """The only shape the chat endpoint accepts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_id: str = Field(
        ...,
        min_length=4,
        max_length=16,
        pattern=CUSTOMER_ID_PATTERN,
        description="Customer identifier, format CUS-XXX.",
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The customer's support request.",
    )
    conversation_id: Optional[str] = Field(
        default=None,
        max_length=32,
        pattern=CONVERSATION_ID_PATTERN,
        description="Existing conversation to continue. Never an authorisation token.",
    )

    @field_validator("message")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message must not be blank")
        if "\x00" in value:
            raise ValueError("message contains an invalid character")
        return cleaned


class AgentActivityItem(BaseModel):
    """Safe operational event for the UI. Never chain-of-thought."""

    step: str
    status: str
    details: Optional[str] = None
    tool_name: Optional[str] = None


class ToolCallItem(BaseModel):
    """Tool name and outcome only - arguments are not echoed back."""

    tool: str
    status: str
    error_code: Optional[str] = None


class ChatData(BaseModel):
    conversation_id: Optional[str] = None
    response: str
    intent: Optional[str] = None
    decision: Optional[str] = None
    resolved: bool = False
    escalated: bool = False
    ticket_id: Optional[str] = None
    error_code: Optional[str] = None
    activity: List[AgentActivityItem] = Field(default_factory=list)
    tool_calls: List[ToolCallItem] = Field(default_factory=list)


class ChatResponse(BaseModel):
    success: bool = True
    data: Optional[ChatData] = None
    message: str = "Request completed successfully."
    request_id: Optional[str] = None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorBody


class HealthData(BaseModel):
    status: str
    version: str
    database: str
    timestamp: str


class HealthResponse(BaseModel):
    success: bool = True
    data: HealthData
    message: str = "Service is operational."
    request_id: Optional[str] = None