"""Agentic AI core for customer support (Phase 1: foundation).

This module contains the deterministic backbone of the agent:

    input validation -> guardrail check -> intent detection ->
    entity extraction -> required-information check

It is deliberately free of any LLM dependency, so it always works without
GOOGLE_API_KEY / GROQ_API_KEY. No factual customer or order data is ever
hardcoded: every fact must come from the tools in
``app.tools.support_tools.TOOLS``, which read the database.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.agents.llm_client import LLMClient
from app.models.models import AgentLog, Conversation, Customer, Message
from app.security.guardrails import (
    GuardrailEngine,
    GuardrailResult,
    ToolPermission,
    guardrail_engine,
)
from app.security.redaction import redact
from app.tools.support_tools import TOOLS, ToolError

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
MAX_MESSAGE_LENGTH = 2000
AGENT_ROLE = "customer"
TOOL_TIMEOUT_SECONDS = 10

ORDER_ID_PATTERN = re.compile(r"\bORD[-\s]?(\d{3,8})\b", re.IGNORECASE)

# Tools that return data belonging to a single order. Every one of them is
# subject to the ownership check, not just get_order.
ORDER_SCOPED_TOOLS = frozenset(
    {
        "get_order",
        "get_delivery_status",
        "get_payment_status",
        "check_refund_status",
        "check_return_eligibility",
    }
)
CUSTOMER_ID_PATTERN = re.compile(r"\bCUS[-\s]?(\d{1,8})\b", re.IGNORECASE)
BARE_ORDER_PATTERN = re.compile(
    r"\border\s*(?:id\s*|number\s*|no\.?\s*|#\s*)?(\d{4,8})\b", re.IGNORECASE
)
ORDER_ID_FORMAT = re.compile(r"^ORD-\d{3,8}$", re.IGNORECASE)
CUSTOMER_ID_FORMAT = re.compile(r"^CUS-\d{1,8}$", re.IGNORECASE)

INTENT_ORDER = [
    "delivery_status",
    "payment_status",
    "refund_status",
    "return_eligibility",
    "order_status",
    "account",
    "general",
]

INTENT_KEYWORDS: Dict[str, Dict[str, int]] = {
    "delivery_status": {
        "where": 2,
        "when will": 3,
        "when is": 2,
        "arrive": 3,
        "arriving": 3,
        "delivery": 2,
        "tracking": 3,
        "shipped": 2,
        "out for delivery": 4,
        "courier": 2,
        "package": 2,
        "dispatch": 2,
        "eta": 2,
    },
    "payment_status": {
        "payment": 3,
        "charged": 3,
        "charge": 2,
        "paid": 2,
        "billing": 3,
        "invoice": 2,
        "card": 2,
        "transaction": 2,
        "money": 1,
    },
    "refund_status": {
        "refund": 4,
        "refunded": 3,
        "money back": 4,
        "reimburse": 3,
        "reimbursement": 3,
        "repaid": 3,
        "cancel": 2,
    },
    "return_eligibility": {
        "return": 3,
        "send back": 3,
        "eligible": 2,
        "exchange": 2,
        "return policy": 4,
        "returning": 2,
        "wrong item": 4,
        "wrong product": 4,
        "incorrect item": 4,
        "damaged": 3,
    },
    "order_status": {
        "order status": 4,
        "order details": 3,
        "order summary": 3,
        "order history": 3,
        "order id": 2,
        "my order": 2,
        "placed": 1,
    },
    "account": {
        "account": 3,
        "login": 3,
        "log in": 3,
        "sign in": 3,
        "profile": 3,
        "email address": 2,
        "username": 2,
    },
    "general": {
        "help": 1,
        "support": 1,
        "question": 1,
        "speak": 2,
        "human": 2,
        "agent": 2,
        "talk": 2,
    },
}

REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    "order_status": ("order_id",),
    "delivery_status": ("order_id",),
    "payment_status": ("order_id",),
    "refund_status": ("order_id",),
    "return_eligibility": ("order_id",),
    "account": ("customer_id",),
    "general": (),
}

MISSING_INFO_MESSAGES: Dict[str, str] = {
    # Format-only placeholders. A concrete seeded identifier must never appear
    # here: these messages are sent to any caller, including one who owns no
    # orders at all, so naming a real order or customer would disclose another
    # account's identifiers and assert a fact no tool verified.
    "MISSING_ORDER_ID": (
        "I can help with that. Please provide your order ID so I can look it up "
        "(it looks like ORD-XXXX)."
    ),
    "MISSING_CUSTOMER_ID": (
        "I can help with that. Please provide your customer ID so I can pull up "
        "your account (it looks like CUS-XXX)."
    ),
}

SAFE_ERROR_MESSAGES: Dict[str, str] = {
    "INVALID_ORDER_ID": (
        "I couldn't find an order with that ID. Please verify the order ID and try again."
    ),
    "ORDER_NOT_ACCESSIBLE": (
        "I couldn't find an order with that ID on your account. Please verify the "
        "order ID and try again."
    ),
    "INVALID_CUSTOMER_ID": "I couldn't find an account with that ID.",
    "INVALID_TOOL_PARAMETERS": (
        "I don't have enough information to complete that request. Please provide "
        "the relevant order ID."
    ),
    "TOOL_NOT_PERMITTED": "I'm not able to perform that action.",
    "TOOL_UNAVAILABLE": "I'm temporarily unable to retrieve that information.",
    "TOOL_TIMEOUT": (
        "That lookup is taking longer than expected. I've stopped waiting - please "
        "try again in a moment."
    ),
    "TOOL_FAILURE": "I'm temporarily unable to retrieve that information.",
    "DATABASE_ERROR": "I'm temporarily unable to retrieve that information.",
}

GENERAL_SAFE_RESPONSE = (
    "I can help with orders, payments, delivery, returns, refunds, and account "
    "questions. Please tell me what you need, and include your order ID if it "
    "relates to a specific order."
)


@dataclass
class AgentIntent:
    """Result of intent detection."""

    primary: str
    scores: Dict[str, int] = field(default_factory=dict)

    @property
    def is_order_related(self) -> bool:
        return self.primary in {
            "order_status",
            "delivery_status",
            "payment_status",
            "refund_status",
            "return_eligibility",
        }


@dataclass
class ExtractedEntities:
    """Identifiers pulled out of the customer's message."""

    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    mentions_charge: bool = False
    mentions_cancellation: bool = False
    claims_not_received: bool = False
    reports_wrong_item: bool = False


class DecisionState(str, Enum):
    """Deterministic outcome of one agent decision point."""

    NEED_MORE_INFO = "need_more_info"
    CONTINUE = "continue"
    COMPLETE = "complete"
    ESCALATE = "escalate"
    ERROR = "error"


@dataclass
class PlannedTool:
    """A tool the agent intends to run, with its bound parameters."""

    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    label: str = ""
    purpose: str = ""

    def safe_params(self) -> Dict[str, Any]:
        return dict(self.params)


@dataclass
class AgentExecutionState:
    """Mutable state carried across the tool-execution loop."""

    intent: AgentIntent
    entities: ExtractedEntities
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    customer_message: str = ""
    plan: List[PlannedTool] = field(default_factory=list)
    queue: List[PlannedTool] = field(default_factory=list)
    executed_tools: List[str] = field(default_factory=list)
    observations: Dict[str, ToolObservation] = field(default_factory=dict)
    iterations: int = 0
    last_decision: DecisionState = DecisionState.CONTINUE


class ToolSelector:
    """Maps a detected intent to an ordered tool plan."""

    SELECTION_MAP: Dict[str, List[str]] = {
        "delivery_status": ["get_order", "get_delivery_status"],
        "payment_status": ["get_order", "get_payment_status"],
        "refund_status": ["get_order", "check_refund_status"],
        "return_eligibility": ["get_order", "check_return_eligibility"],
        "order_status": ["get_order"],
        "account": ["get_customer"],
        "general": [],
    }

    # Single source of truth: the module-level ORDER_SCOPED_TOOLS. Duplicating
    # this list would let a new order-scoped tool bypass enforce_ownership.
    ORDER_SCOPED_TOOLS = ORDER_SCOPED_TOOLS

    PURPOSE = {
        "get_order": "Verify the order exists and read its verified status",
        "get_delivery_status": "Check the verified delivery status",
        "get_payment_status": "Check the verified payment status",
        "check_refund_status": "Check the verified refund status",
        "check_return_eligibility": "Check verified return eligibility",
        "get_customer": "Read verified account information",
        "create_support_ticket": "Escalate to the support team",
    }

    @classmethod
    def plan(
        cls, intent: AgentIntent, entities: ExtractedEntities
    ) -> List[PlannedTool]:
        """Build the ordered tool plan for this turn.

        The base plan comes from the intent mapping. Extra tools are appended
        only when the customer's own wording implies a second verified fact
        (for example being charged for a cancelled order).
        """
        names: List[str] = list(cls.SELECTION_MAP.get(intent.primary, []))

        wants_order_tools = bool(names) and intent.is_order_related

        if wants_order_tools or entities.claims_not_received or entities.reports_wrong_item:
            if entities.mentions_charge and "get_payment_status" not in names:
                names.append("get_payment_status")
            if entities.mentions_cancellation and "check_refund_status" not in names:
                names.append("check_refund_status")
            if (
                entities.claims_not_received or entities.reports_wrong_item
            ) and "get_delivery_status" not in names:
                names.append("get_delivery_status")
            if (
                entities.reports_wrong_item
                and "check_return_eligibility" not in names
            ):
                names.append("check_return_eligibility")

        if (
            entities.order_id
            and "get_order" not in names
            and any(name in cls.ORDER_SCOPED_TOOLS for name in names)
        ):
            names.insert(0, "get_order")

        planned: List[PlannedTool] = []
        for name in names:
            if name == "get_customer":
                params = {"customer_id": entities.customer_id} if entities.customer_id else {}
            else:
                params = {"order_id": entities.order_id} if entities.order_id else {}
            planned.append(
                PlannedTool(
                    name=name,
                    params=params,
                    label=cls.PURPOSE.get(name, name),
                    purpose=name,
                )
            )
        return planned


@dataclass
class ToolObservation:
    """Verified outcome of a single tool execution."""

    tool: str
    params: Dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    data: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class AgentResult:
    """Outcome of one agent turn, returned to the API layer."""

    success: bool = False
    response: str = ""
    intent: Optional[str] = None
    needs_more_info: bool = False
    blocked: bool = False
    threat_type: Optional[str] = None
    resolved: bool = False
    escalated: bool = False
    decision: Optional["DecisionState"] = None
    ticket_id: Optional[str] = None
    conversation_id: Optional[str] = None
    error_code: Optional[str] = None
    cleaned_message: Optional[str] = None
    normalized_customer_id: Optional[str] = None
    activity: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    observations: Dict[str, ToolObservation] = field(default_factory=dict)

    def add_activity(
        self,
        step: str,
        status: str = "completed",
        details: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> None:
        self.activity.append(
            {"step": step, "status": status, "details": details, "tool_name": tool_name}
        )


@dataclass
class AgentOutcome:
    """Final result of one agent turn, as returned by run_support_agent().

    This wraps the AgentResult rather than copying it, so the two structures
    can never drift apart. Access the underlying result with `.result`.
    """

    result: AgentResult
    decision: Optional[DecisionState] = None
    intent: Optional[str] = None
    verified_facts: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(
        cls, result: AgentResult, verified_facts: Optional[Dict[str, Any]] = None
    ) -> "AgentOutcome":
        return cls(
            result=result,
            decision=result.decision,
            intent=result.intent,
            verified_facts=verified_facts or {},
        )

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def response(self) -> str:
        return self.result.response

    @property
    def resolved(self) -> bool:
        return self.result.resolved

    @property
    def escalated(self) -> bool:
        return self.result.escalated

    @property
    def needs_more_info(self) -> bool:
        return self.result.needs_more_info

    @property
    def blocked(self) -> bool:
        return self.result.blocked

    @property
    def ticket_id(self) -> Optional[str]:
        return self.result.ticket_id

    @property
    def error_code(self) -> Optional[str]:
        return self.result.error_code

    @property
    def conversation_id(self) -> Optional[str]:
        return self.result.conversation_id

    @property
    def tool_calls(self) -> List[Dict[str, Any]]:
        return self.result.tool_calls

    @property
    def observations(self) -> Dict[str, ToolObservation]:
        return self.result.observations

    @property
    def activity(self) -> List[Dict[str, Any]]:
        return self.result.activity

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe view for the API layer."""
        facts: Dict[str, Any] = {}
        for tool, observation in self.result.observations.items():
            facts[tool] = observation.data if observation.ok else {
                "error_code": observation.error_code
            }
        return {
            "success": self.result.success,
            "response": self.result.response,
            "intent": self.result.intent,
            "decision": self.decision.value if self.decision else None,
            "resolved": self.result.resolved,
            "escalated": self.result.escalated,
            "needs_more_info": self.result.needs_more_info,
            "blocked": self.result.blocked,
            "ticket_id": self.result.ticket_id,
            "error_code": self.result.error_code,
            "conversation_id": self.result.conversation_id,
            "tool_calls": self.result.tool_calls,
            "verified_facts": facts,
            "activity": self.result.activity,
        }


class ResponseBuilder:
    """Builds customer-facing replies from verified tool output only.

    Every sentence here is derived from a ToolObservation that actually
    succeeded. Missing facts are reported as unavailable, never guessed.
    """

    ORDER_SENTENCES = {
        "pending": "is awaiting processing",
        "confirmed": "is confirmed",
        "processing": "is being processed",
        "shipped": "has been shipped",
        "out_for_delivery": "is out for delivery",
        "delivered": "has been delivered",
        "cancelled": "was cancelled",
        "returned": "was returned",
        "refunded": "was refunded",
    }

    DELIVERY_SENTENCES = {
        "pending": "Delivery has not been dispatched yet.",
        "shipped": "Your parcel is with the carrier and is on its way.",
        "out_for_delivery": "Your parcel is out for delivery today.",
        "delivered": "Our records show this parcel as delivered.",
        "failed": "The delivery attempt was unsuccessful.",
        "returned": "The parcel was returned to us.",
    }

    PAYMENT_SENTENCES = {
        "completed": "The payment for this order was completed successfully.",
        "pending": "The payment for this order is still pending.",
        "failed": "The payment for this order did not go through.",
        "refunded": "The payment for this order has been refunded.",
        "partially_refunded": "The payment for this order has been partially refunded.",
    }

    REFUND_SENTENCES = {
        "refunded": "The refund has been completed.",
        "processing": "The refund is currently being processed.",
        "approved": "The refund has been approved.",
        "requested": "A refund has been requested and is awaiting review.",
        "none": "I could not find a refund recorded against this order.",
        "no_refund_info": "I could not find any refund information for this order.",
    }

    @staticmethod
    def humanize(value: Optional[str]) -> str:
        if not value:
            return "unknown"
        return str(value).replace("_", " ")

    @staticmethod
    def format_date(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            from datetime import datetime as _dt

            parsed = _dt.fromisoformat(str(value))
            return parsed.strftime("%d %B %Y at %H:%M").lstrip("0")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def format_money(amount: Any, currency: Optional[str]) -> Optional[str]:
        if amount is None:
            return None
        try:
            return f"{float(amount):,.2f} {currency or ''}".strip()
        except (TypeError, ValueError):
            return None

    @classmethod
    def order_sentence(cls, data: Dict[str, Any]) -> str:
        order_id = data.get("order_id")
        status = data.get("status")
        text = cls.ORDER_SENTENCES.get(
            status, f"has the verified status '{cls.humanize(status)}'"
        )
        prefix = f"Your order {order_id} " if order_id else "Your order "
        total = cls.format_money(data.get("total"), data.get("currency"))
        if total:
            text = f"{text} (total {total})"
        return prefix + text + "."

    @classmethod
    def _delivery_text(cls, data: Dict[str, Any]) -> str:
        status = data.get("status")
        parts = [cls.DELIVERY_SENTENCES.get(status, f"Delivery status: {cls.humanize(status)}.")]

        carrier = data.get("carrier")
        tracking = data.get("tracking_number")
        if carrier and tracking:
            parts.append(f"Carrier: {carrier} (tracking {tracking}).")
        elif tracking:
            parts.append(f"Tracking number: {tracking}.")

        estimated = cls.format_date(data.get("estimated_delivery"))
        actual = cls.format_date(data.get("actual_delivery"))
        if actual:
            parts.append(f"Delivered on {actual}.")
        elif estimated:
            parts.append(f"Estimated delivery: {estimated}.")
        return " ".join(parts)

    @classmethod
    def _payment_text(cls, data: Dict[str, Any]) -> str:
        status = data.get("status")
        sentence = cls.PAYMENT_SENTENCES.get(status, f"Payment status: {cls.humanize(status)}.")
        amount = cls.format_money(data.get("amount"), data.get("currency"))
        if amount:
            sentence = f"{sentence} Amount: {amount}."
        return sentence

    @classmethod
    def _refund_text(cls, data: Dict[str, Any]) -> str:
        status = data.get("status")
        sentence = cls.REFUND_SENTENCES.get(status, f"Refund status: {cls.humanize(status)}.")
        amount = cls.format_money(data.get("amount"), None)
        if amount and status in {"refunded", "processing", "approved"}:
            sentence = f"{sentence} Refund amount: {amount}."
        return sentence

    @classmethod
    def _return_text(cls, data: Dict[str, Any]) -> str:
        if data.get("eligible"):
            remaining = data.get("return_window_days")
            text = "This order is eligible for return."
            if isinstance(remaining, int):
                text = f"{text} You have {remaining} day(s) left in the return window."
            return text
        reason = data.get("reason")
        if reason:
            return f"This order is not eligible for return: {reason}."
        return "This order is not eligible for return."

    @classmethod
    def _account_text(cls, data: Dict[str, Any]) -> str:
        parts = [f"Your account ({data.get('customer_id')}) is active and in good standing."]
        orders = data.get("recent_orders") or []
        if orders:
            recent = ", ".join(
                f"{o.get('order_id')} ({cls.humanize(o.get('status'))})" for o in orders[:3]
            )
            parts.append(f"Your most recent orders are: {recent}.")
        tickets = data.get("open_tickets") or []
        if tickets:
            parts.append(f"You have {len(tickets)} open support ticket(s).")
        return " ".join(parts)

    @classmethod
    def build(cls, state: "AgentExecutionState") -> str:
        """Compose the reply from verified observations."""
        order = state.observations.get("get_order")
        delivery = state.observations.get("get_delivery_status")
        payment = state.observations.get("get_payment_status")
        refund = state.observations.get("check_refund_status")
        returns = state.observations.get("check_return_eligibility")
        customer = state.observations.get("get_customer")

        for observation in (order, delivery, payment, refund, returns, customer):
            if observation is not None and not observation.ok:
                return SAFE_ERROR_MESSAGES.get(
                    observation.error_code or "TOOL_FAILURE",
                    "I'm temporarily unable to retrieve that information.",
                )

        parts: List[str] = []
        if order is not None and order.ok:
            parts.append(cls.order_sentence(order.data or {}))

        if delivery is not None and delivery.ok:
            if delivery.data and delivery.data.get("status") in {"no_delivery_info"}:
                parts.append(
                    "I could not find any delivery information recorded for this order yet."
                )
            else:
                parts.append(cls._delivery_text(delivery.data or {}))

        if payment is not None and payment.ok:
            if payment.data and payment.data.get("status") == "no_payment_info":
                parts.append("I could not find any payment information for this order.")
            else:
                parts.append(cls._payment_text(payment.data or {}))

        if refund is not None and refund.ok:
            parts.append(cls._refund_text(refund.data or {}))

        if returns is not None and returns.ok:
            parts.append(cls._return_text(returns.data or {}))

        if customer is not None and customer.ok:
            parts.append(cls._account_text(customer.data or {}))

        if not parts:
            return SAFE_ERROR_MESSAGES["TOOL_FAILURE"]
        return " ".join(parts)


class AgentMemory:
    """Conversation memory and structured agent logging.

    Uses the project's existing SQLAlchemy models (Conversation, Message,
    AgentLog) through the existing session factory. Every method is failure
    tolerant: if the database is unavailable the agent still answers, it just
    cannot remember or log.

    The session factory is resolved lazily on each call so tests can redirect
    it, and so runtime configuration changes are honoured.
    """

    HISTORY_LIMIT = 8
    MAX_MESSAGE_CHARS = 4000

    LOG_STATUS_MAP = {"completed": "success", "success": "success", "error": "error"}

    EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    PHONE_PATTERN = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
    ORDER_IN_TEXT = re.compile(r"\bORD[-\s]?(\d{3,8})\b", re.IGNORECASE)

    def __init__(self, history_limit: int = HISTORY_LIMIT) -> None:
        self.history_limit = history_limit
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------ helpers
    def _session(self):
        import app.database.session as session_module

        return session_module.SessionLocal()

    @classmethod
    def redact(cls, text: str) -> str:
        """Mask contact details before anything is written to the database."""
        if not text:
            return text
        text = cls.EMAIL_PATTERN.sub("[email-redacted]", text)
        text = cls.PHONE_PATTERN.sub("[phone-redacted]", text)
        return text

    def _log(self, error_code: str) -> Dict[str, Any]:
        self.last_error = error_code
        logger.warning("AgentMemory failure: %s", error_code)
        return {"conversation_id": None, "is_new": False, "error_code": error_code}

    @staticmethod
    def _conversation_pk(db: Any, conversation_id: str) -> Optional[int]:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.conversation_id == conversation_id)
            .first()
        )
        return conversation.id if conversation else None

    # ------------------------------------------------------- conversations
    def get_or_create_conversation(
        self, customer_id: Optional[str], conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Find the caller's conversation or start a new one."""
        db = None
        try:
            db = self._session()
            if not customer_id:
                return {
                    "conversation_id": None,
                    "is_new": False,
                    "error_code": "MISSING_CUSTOMER_ID",
                }

            customer = (
                db.query(Customer)
                .filter(Customer.customer_id == customer_id)
                .first()
            )
            if customer is None:
                return {
                    "conversation_id": None,
                    "is_new": False,
                    "error_code": "INVALID_CUSTOMER_ID",
                }

            if conversation_id:
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == conversation_id)
                    .first()
                )
                if conversation is None:
                    return {
                        "conversation_id": None,
                        "is_new": False,
                        "error_code": "INVALID_CONVERSATION_ID",
                    }
                if conversation.customer_id != customer.id:
                    return {
                        "conversation_id": None,
                        "is_new": False,
                        "error_code": "CONVERSATION_NOT_ACCESSIBLE",
                    }
                return {
                    "conversation_id": conversation.conversation_id,
                    "is_new": False,
                    "error_code": None,
                }

            for _ in range(5):
                candidate = f"CONV-{random.randint(100000, 999999)}"
                clash = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == candidate)
                    .first()
                )
                if clash is None:
                    conversation = Conversation(
                        conversation_id=candidate,
                        customer_id=customer.id,
                        status="active",
                    )
                    db.add(conversation)
                    db.commit()
                    db.refresh(conversation)
                    return {
                        "conversation_id": conversation.conversation_id,
                        "is_new": True,
                        "error_code": None,
                    }
            return self._log("CONVERSATION_CREATE_FAILED")
        except Exception:
            logger.exception("Conversation lookup failed")
            return self._log("DATABASE_ERROR")
        finally:
            if db is not None:
                db.close()

    def load_recent_messages(
        self, conversation_id: Optional[str], limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return previous messages, oldest first. Never raises."""
        if not conversation_id:
            return []
        db = None
        try:
            db = self._session()
            rows = (
                db.query(Message)
                .filter(Message.conversation_id == self._conversation_pk(db, conversation_id))
                .order_by(Message.id.desc())
                .limit(limit or self.history_limit)
                .all()
            )
            history = [
                {
                    "role": row.role,
                    "content": row.content,
                    "intent": row.intent,
                }
                for row in reversed(rows)
            ]
            return history
        except Exception:
            logger.exception("Could not load conversation history")
            return []
        finally:
            if db is not None:
                db.close()

    def remember_order_id(self, conversation_id: Optional[str]) -> Optional[str]:
        """Recover the most recently mentioned order ID from history."""
        for entry in reversed(self.load_recent_messages(conversation_id)):
            matches = self.ORDER_IN_TEXT.findall(entry.get("content") or "")
            if matches:
                return f"ORD-{matches[-1]}"
        return None

    # ----------------------------------------------------------- messages
    def _add_message(
        self,
        conversation_id: Optional[str],
        role: str,
        content: str,
        intent: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_results: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not conversation_id or not content:
            return False
        db = None
        try:
            db = self._session()
            pk = self._conversation_pk(db, conversation_id)
            if pk is None:
                return False
            message = Message(
                conversation_id=pk,
                role=role,
                content=self.redact(content[: self.MAX_MESSAGE_CHARS]),
                intent=intent,
                tool_calls=json.dumps(tool_calls) if tool_calls else None,
                tool_results=(
                    json.dumps(self.redact(json.dumps(tool_results)))
                    if tool_results
                    else None
                ),
            )
            db.add(message)
            db.commit()
            return True
        except Exception:
            logger.exception("Could not persist %s message", role)
            return False
        finally:
            if db is not None:
                db.close()

    def add_user_message(
        self,
        conversation_id: Optional[str],
        content: str,
        intent: Optional[str] = None,
    ) -> bool:
        return self._add_message(conversation_id, "user", content, intent=intent)

    def add_agent_message(
        self,
        conversation_id: Optional[str],
        content: str,
        intent: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_results: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return self._add_message(
            conversation_id,
            "assistant",
            content,
            intent=intent,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

    # ------------------------------------------------------------- logging
    def log_event(
        self,
        conversation_id: Optional[str],
        action: str,
        tool_name: Optional[str] = None,
        tool_input: Optional[Any] = None,
        tool_output: Optional[Any] = None,
        status: str = "success",
        error_message: Optional[str] = None,
    ) -> bool:
        """Write one structured operational event. Never raises."""
        if not conversation_id:
            return False
        db = None
        try:
            db = self._session()
            pk = self._conversation_pk(db, conversation_id)
            if pk is None:
                return False

            def _dump(value: Any) -> Optional[str]:
                if value is None:
                    return None
                if isinstance(value, str):
                    return self.redact(value)[:1000]
                return self.redact(json.dumps(value, default=str))[:1000]

            entry = AgentLog(
                conversation_id=pk,
                action=action[:100],
                tool_name=(tool_name or "")[:50] or None,
                tool_input=_dump(tool_input),
                tool_output=_dump(tool_output),
                status=status[:20],
                error_message=(error_message or "")[:500] or None,
            )
            db.add(entry)
            db.commit()
            return True
        except Exception:
            logger.exception("Could not write agent log")
            return False
        finally:
            if db is not None:
                db.close()

    def log_run(self, conversation_id: Optional[str], result: AgentResult) -> None:
        """Persist the operational trace of one completed turn.

        Only operational facts are stored (intent, tools, decisions, outcome).
        No reasoning text and no internal prompts are written.
        """
        if not conversation_id:
            return

        self.log_event(
            conversation_id,
            action="request_received",
            tool_input={"intent": result.intent},
            status="success" if result.success else "error",
        )

        for entry in result.activity:
            step = str(entry.get("step", ""))
            status = self.LOG_STATUS_MAP.get(
                str(entry.get("status", "completed")), "success"
            )
            tool_name = entry.get("tool_name")
            details = entry.get("details")

            if step.startswith("Intent identified"):
                action = "intent_detected"
            elif step.startswith("Tool plan"):
                action = "tools_selected"
                tool_name = None
            elif step.startswith("Tool executed"):
                action = "tool_executed"
            elif "ticket" in step.lower():
                action = "escalation"
            elif step.startswith("Security check") and status == "error":
                action = "security_blocked"
            else:
                action = re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_")[:60]

            output: Any = None
            if tool_name and tool_name in result.observations:
                observation = result.observations[tool_name]
                output = (
                    observation.data if observation.ok else {"error_code": observation.error_code}
                )

            self.log_event(
                conversation_id,
                action=action,
                tool_name=tool_name,
                tool_input=details if action in {"tool_executed"} else {"detail": details},
                tool_output=output,
                status=status,
                error_message=details if status == "error" else None,
            )

        if result.decision is not None:
            self.log_event(
                conversation_id,
                action="decision",
                tool_input={"decision": result.decision.value},
                status="success",
            )

        self.log_event(
            conversation_id,
            action="final_resolution",
            tool_input={
                "resolved": result.resolved,
                "escalated": result.escalated,
                "ticket_id": result.ticket_id,
            },
            tool_output={"error_code": result.error_code},
            status="success" if result.success else "error",
        )


class InputValidator:
    """Validates and normalises raw customer input."""

    @staticmethod
    def clean(message: Any, customer_id: Any = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (message, customer_id, error_code). Never raises."""
        if not isinstance(message, str):
            return None, None, "INVALID_MESSAGE"

        cleaned = message.replace("\x00", "").strip()
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        if not cleaned:
            return None, None, "EMPTY_MESSAGE"
        if len(cleaned) > MAX_MESSAGE_LENGTH:
            return None, None, "MESSAGE_TOO_LONG"

        normalized_customer = None
        if customer_id is not None:
            if not isinstance(customer_id, str):
                return None, None, "INVALID_CUSTOMER_ID"
            normalized_customer = customer_id.strip().upper()
            if normalized_customer and not CUSTOMER_ID_FORMAT.match(normalized_customer):
                return None, None, "INVALID_CUSTOMER_ID"

        return cleaned, normalized_customer, None


class IntentClassifier:
    """Deterministic intent detection (no LLM required)."""

    @staticmethod
    def detect(message: str) -> AgentIntent:
        text = message.lower()
        scores: Dict[str, int] = {}
        for intent, keywords in INTENT_KEYWORDS.items():
            score = 0
            for keyword, weight in keywords.items():
                if keyword in text:
                    score += weight
            scores[intent] = score

        best = max(scores.values()) if scores else 0
        if best <= 0:
            return AgentIntent(primary="general", scores=scores)

        for intent in INTENT_ORDER:
            if scores.get(intent, 0) == best:
                return AgentIntent(primary=intent, scores=scores)
        return AgentIntent(primary="general", scores=scores)


class EntityExtractor:
    """Extracts order/customer identifiers and issue signals."""

    @staticmethod
    def extract(message: str, customer_id: Optional[str] = None) -> ExtractedEntities:
        text = message.lower()

        order_match = ORDER_ID_PATTERN.search(message)
        if order_match:
            order_id = f"ORD-{order_match.group(1)}"
        else:
            bare_match = BARE_ORDER_PATTERN.search(message)
            order_id = f"ORD-{bare_match.group(1)}" if bare_match else None

        if order_id and not ORDER_ID_FORMAT.match(order_id):
            order_id = None

        customer_match = CUSTOMER_ID_PATTERN.search(message)
        extracted_customer = (
            f"CUS-{customer_match.group(1)}" if customer_match else None
        )

        return ExtractedEntities(
            order_id=order_id,
            customer_id=customer_id or extracted_customer,
            mentions_charge=any(
                phrase in text
                for phrase in ("charged", "charge", "paid", "payment", "billed", "debit")
            ),
            mentions_cancellation=any(
                phrase in text for phrase in ("cancel", "cancelled", "canceled")
            ),
            claims_not_received=any(
                phrase in text
                for phrase in (
                    "not received",
                    "never received",
                    "didn't receive",
                    "did not receive",
                    "not delivered",
                    "never arrived",
                    "not arrived",
                    "hasn't arrived",
                    "has not arrived",
                    "missing",
                )
            ),
            reports_wrong_item=any(
                phrase in text
                for phrase in ("wrong item", "wrong product", "wrong order", "damaged", "incorrect")
            ),
        )


class SupportAgent:
    """Agentic customer support resolution agent (Phase 1 foundation)."""

    def __init__(
        self,
        tools: Optional[Dict[str, Any]] = None,
        guardrail: Optional[GuardrailEngine] = None,
        max_iterations: int = MAX_TOOL_ITERATIONS,
        tool_timeout: int = TOOL_TIMEOUT_SECONDS,
        llm: Optional[LLMClient] = None,
    ) -> None:
        self.tools = tools if tools is not None else TOOLS
        self.guardrail = guardrail if guardrail is not None else guardrail_engine
        self.max_iterations = max_iterations
        self.tool_timeout = tool_timeout
        self.llm = llm

    def check_tool_permission(self, tool_name: str) -> Tuple[bool, str]:
        """Verify a tool is allow-listed and permitted for this role."""
        if tool_name not in self.tools:
            return False, f"Tool '{tool_name}' is not available to the agent."
        permission = ToolPermission.check_permission(tool_name, role=AGENT_ROLE)
        if not permission.get("allowed"):
            return False, permission.get("reason", "Tool use is not permitted.")
        return True, ""

    def validate(self, message: Any, customer_id: Any = None) -> AgentResult:
        """Validate input and run the guardrail. Returns a blocked result on failure."""
        cleaned, normalized_customer, error_code = InputValidator.clean(message, customer_id)

        if error_code:
            response = {
                "EMPTY_MESSAGE": "Please type your question so I can help.",
                "MESSAGE_TOO_LONG": "Your message is too long. Please shorten it and try again.",
                "INVALID_MESSAGE": "I could not read that message. Please rephrase it.",
                "INVALID_CUSTOMER_ID": "That customer ID does not look valid. Expected a format like CUS-XXX.",
            }.get(error_code, "I could not process that request.")
            result = AgentResult(
                success=False,
                response=response,
                error_code=error_code,
            )
            result.add_activity("Input validation failed", "error", error_code)
            return result

        guardrail_result: GuardrailResult = self.guardrail.check_input(cleaned)
        if not guardrail_result.safe:
            result = AgentResult(
                success=False,
                response=self.guardrail.get_safe_response(guardrail_result.threat_type),
                blocked=True,
                threat_type=(
                    guardrail_result.threat_type.value
                    if guardrail_result.threat_type
                    else None
                ),
                error_code="BLOCKED_BY_GUARDRAIL",
            )
            result.add_activity(
                "Security check blocked the request",
                "error",
                guardrail_result.threat_type.value if guardrail_result.threat_type else None,
            )
            logger.warning(
                "Guardrail blocked input: threat=%s risk=%s",
                guardrail_result.threat_type,
                guardrail_result.risk_level,
            )
            return result

        result = AgentResult(
            success=True,
            cleaned_message=cleaned,
            normalized_customer_id=normalized_customer,
        )
        result.add_activity("Input received and validated")
        result.add_activity(
            "Security check passed",
            "completed",
            "no threats detected",
        )
        return result

    def detect_intent(self, message: str) -> AgentIntent:
        intent = IntentClassifier.detect(message)
        return intent

    def extract_entities(
        self, message: str, customer_id: Optional[str] = None
    ) -> ExtractedEntities:
        return EntityExtractor.extract(message, customer_id)

    def llm_enabled(self) -> bool:
        """True only when an LLM client was supplied and has a provider."""
        return bool(self.llm is not None and self.llm.available())

    def _is_uncertain(self, intent: AgentIntent) -> bool:
        """Deterministic intent is uncertain when nothing scored or a tie occurred."""
        scores = intent.scores or {}
        if not scores:
            return True
        top = max(scores.values())
        if top <= 0:
            return True
        return list(scores.values()).count(top) > 1

    def refine_intent(self, message: str, intent: AgentIntent) -> AgentIntent:
        """Optional LLM assist when the deterministic classifier is unsure.

        The model may only pick between the labels the deterministic
        classifier already considered. It never sees customer records and can
        never change a confident deterministic decision.
        """
        if not self.llm_enabled() or not self._is_uncertain(intent):
            return intent

        top = max((intent.scores or {}).values() or [0])
        candidates = [
            label for label in INTENT_ORDER if (intent.scores or {}).get(label, 0) == top
        ] or list(INTENT_ORDER)

        result = self.llm.classify_intent(message, candidate_labels=candidates)
        if not result.ok or not isinstance(result.value, dict):
            logger.info("LLM intent assist unavailable: %s", result.error_code)
            return intent

        label = result.value.get("intent")
        if label not in candidates:
            return intent
        refined = dict(intent.scores or {})
        refined[label] = top + 1
        return AgentIntent(primary=label, scores=refined)

    def verified_facts(self, state: AgentExecutionState) -> Dict[str, Any]:
        """Verified tool output only, for the LLM and for logging."""
        facts: Dict[str, Any] = {}
        for tool, observation in state.observations.items():
            if observation.ok and observation.data:
                facts[tool] = observation.data
        return facts

    # Direct-contact fields returned by get_customer. They are never used to
    # build the reply, so they must not be shipped to a third-party LLM
    # provider. They remain in verified_facts for server-side logging.
    LLM_UNSAFE_FACT_KEYS = frozenset(
        {"name", "email", "phone", "address", "contact", "contact_info"}
    )

    def llm_safe_facts(self, state: AgentExecutionState) -> Dict[str, Any]:
        """Verified facts with direct-contact PII removed.

        The reply templates are built from order, delivery, payment and refund
        fields only, so nothing here depends on customer contact details.
        """
        facts = self.verified_facts(state)
        cleaned: Dict[str, Any] = {}
        for tool, data in facts.items():
            if isinstance(data, dict):
                cleaned[tool] = {
                    key: value
                    for key, value in data.items()
                    if key.lower() not in self.LLM_UNSAFE_FACT_KEYS
                }
            else:
                cleaned[tool] = data
        return cleaned

    def polish_response(self, result: AgentResult, state: AgentExecutionState) -> None:
        """Optionally rephrase the verified reply. Never changes the facts.

        The deterministic ResponseBuilder output stays authoritative: if the
        model is unavailable, times out, or introduces an unverified claim,
        the original verified reply is kept.
        """
        if not self.llm_enabled() or not result.response:
            return

        facts = self.llm_safe_facts(state)
        if not facts:
            return

        rewritten = self.llm.generate_response(
            customer_message=redact(state.customer_message or "", limit=2000),
            verified_facts=facts,
            base_response=result.response,
        )
        if rewritten.ok and isinstance(rewritten.value, str) and rewritten.value.strip():
            result.response = rewritten.value.strip()
            result.add_activity(
                "Reply phrasing refined by language model",
                "completed",
                f"provider={rewritten.provider}",
            )
        else:
            result.add_activity(
                "Kept deterministic reply",
                "completed",
                f"llm={rewritten.error_code or 'unavailable'}",
            )

    def check_required_info(
        self,
        intent: AgentIntent,
        entities: ExtractedEntities,
        remembered_order_id: Optional[str] = None,
    ) -> Tuple[List[str], Optional[str]]:
        """Return (missing_fields, resolved_order_id)."""
        missing: List[str] = []
        resolved_order_id = entities.order_id or remembered_order_id

        for required in REQUIRED_FIELDS.get(intent.primary, ()):
            if required == "order_id" and not resolved_order_id:
                missing.append("order_id")
            elif required == "customer_id" and not entities.customer_id:
                missing.append("customer_id")

        return missing, resolved_order_id

    def plan_tools(
        self, intent: AgentIntent, entities: ExtractedEntities
    ) -> List[PlannedTool]:
        """Return the ordered tool plan for this turn."""
        return ToolSelector.plan(intent, entities)

    def execute_tool(self, planned: PlannedTool) -> ToolObservation:
        """Run one planned tool through the registry and capture the outcome.

        This method never raises: every failure becomes a ToolObservation so
        the loop can decide what to do next without losing the error.
        """
        params = planned.safe_params()
        allowed, reason = self.check_tool_permission(planned.name)
        if not allowed:
            return ToolObservation(
                tool=planned.name,
                params=params,
                ok=False,
                error_code="TOOL_NOT_PERMITTED",
                error_message=reason,
            )

        spec = self.tools.get(planned.name) or {}
        required = (spec.get("parameters") or {}).get("required", [])
        missing_params = [name for name in required if not params.get(name)]
        if missing_params:
            return ToolObservation(
                tool=planned.name,
                params=params,
                ok=False,
                error_code="INVALID_TOOL_PARAMETERS",
                error_message=(
                    "Missing required parameter(s): " + ", ".join(missing_params)
                ),
            )

        function = spec.get("function")
        if function is None:
            return ToolObservation(
                tool=planned.name,
                params=params,
                ok=False,
                error_code="TOOL_UNAVAILABLE",
                error_message="The tool has no executable implementation.",
            )

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(function, **params)
        try:
            data = future.result(timeout=self.tool_timeout)
            payload = data if isinstance(data, dict) else {"result": data}
            return ToolObservation(
                tool=planned.name, params=params, ok=True, data=payload
            )
        except FutureTimeoutError:
            future.cancel()
            return ToolObservation(
                tool=planned.name,
                params=params,
                ok=False,
                error_code="TOOL_TIMEOUT",
                error_message="The lookup exceeded the allowed time.",
            )
        except ToolError as exc:
            logger.info("Tool %s failed: %s", planned.name, exc.error_code)
            return ToolObservation(
                tool=planned.name,
                params=params,
                ok=False,
                error_code=exc.error_code,
                error_message=exc.message,
            )
        except Exception:
            logger.exception("Unexpected failure in tool %s", planned.name)
            return ToolObservation(
                tool=planned.name,
                params=params,
                ok=False,
                error_code="TOOL_FAILURE",
                error_message="An internal error occurred while running this operation.",
            )
        finally:
            executor.shutdown(wait=False)

    def _access_denied(
        self, observation: ToolObservation, reason: str
    ) -> ToolObservation:
        """Neutral denial for order-scoped data.

        The error is intentionally identical whether the order does not exist or
        belongs to somebody else, so the response cannot be used to enumerate
        valid order IDs.
        """
        logger.warning(
            "Ownership check denied for %s: %s", observation.tool, reason
        )
        return ToolObservation(
            tool=observation.tool,
            params=observation.params,
            ok=False,
            error_code="ORDER_NOT_ACCESSIBLE",
            error_message="The order is not associated with this customer.",
        )

    def enforce_ownership(
        self, observation: ToolObservation, state: AgentExecutionState
    ) -> ToolObservation:
        """Block cross-account access to another customer's order.

        Fails closed. Order-scoped data is only released when a customer
        identity is present and the recorded order owner matches it.
        """
        if not observation.ok or observation.tool not in ORDER_SCOPED_TOOLS:
            return observation

        # No verified identity: refuse rather than release the data.
        if not state.customer_id:
            return self._access_denied(observation, "no verified customer identity")

        verified = state.observations.get("get_order")
        if observation.tool != "get_order" and (verified is None or not verified.ok):
            return self._access_denied(observation, "order ownership not verified")

        order_owner = (observation.data or {}).get("customer_id")
        if observation.tool != "get_order":
            order_owner = ((verified.data or {}) if verified else {}).get("customer_id")

        # Fail closed: an absent owner is not a match. If the order row carries
        # no customer (or the tool returned a shape without one), the data is
        # NOT released.
        if not order_owner or order_owner.upper() != state.customer_id.upper():
            return self._access_denied(observation, "order belongs to another customer")
        return observation

    def _verified_claim_resolution(self, state: AgentExecutionState) -> bool:
        """True only when verified tool output fully answers a damage/wrong-item claim.

        A claim is auto-resolved only when a successful tool call proves the
        request can be answered from data. The reply is then composed by
        ResponseBuilder from those same verified observations, so no resolution
        information can be invented. Anything missing, failed or inconclusive
        keeps the previous behaviour and hands the case to a human.
        """
        order = state.observations.get("get_order")
        if order is None or not order.ok:
            # Ownership was never proven, so nothing may be released.
            return False

        if state.entities.claims_not_received:
            # A lost or undelivered parcel needs a refund or a re-delivery, and
            # the agent has no tool for either. A verified delivery record can
            # explain where a parcel is, but it cannot close the claim.
            return False

        returns = state.observations.get("check_return_eligibility")
        if returns is None or not returns.ok:
            return False

        # Only a positive, verified eligibility answer closes the request.
        # Ineligible (window expired, already returned, not delivered) means the
        # customer still needs a human, exactly as before.
        return (returns.data or {}).get("eligible") is True

    def decide(
        self, state: AgentExecutionState, pending_count: int
    ) -> DecisionState:
        """Deterministic decision taken after each observation.

        CONTINUE   -> another verified fact is still required
        COMPLETE   -> the plan finished and the verified facts answer the request
        ESCALATE   -> the request is understood but cannot be auto-resolved
        ERROR      -> a tool failed in a way that makes further work pointless
        NEED_MORE_INFO -> handled before the loop starts
        """
        if state.iterations >= self.max_iterations:
            return DecisionState.ERROR

        if state.executed_tools:
            last = state.observations.get(state.executed_tools[-1])
            if last is not None and not last.ok:
                if last.error_code in {
                    "INVALID_ORDER_ID",
                    "ORDER_NOT_ACCESSIBLE",
                    "TOOL_NOT_PERMITTED",
                    "TOOL_UNAVAILABLE",
                    "INVALID_TOOL_PARAMETERS",
                }:
                    return DecisionState.ERROR
                order_ok = bool(
                    (state.observations.get("get_order") or ToolObservation(tool="")).ok
                )
                if last.tool != "get_order" and order_ok:
                    return DecisionState.CONTINUE
                return DecisionState.ERROR

        if pending_count <= 0:
            if state.entities.claims_not_received or state.entities.reports_wrong_item:
                # Verified observations decide the outcome: a claim is only
                # auto-resolved when a successful tool call answers it, and it is
                # escalated whenever the verified data cannot close it.
                if self._verified_claim_resolution(state):
                    return DecisionState.COMPLETE
                return DecisionState.ESCALATE
            return DecisionState.COMPLETE

        return DecisionState.CONTINUE

    def _record_observation(
        self, result: AgentResult, observation: ToolObservation
    ) -> None:
        result.observations[observation.tool] = observation
        result.tool_calls.append(
            {
                "tool": observation.tool,
                "params": dict(observation.params),
                "status": "success" if observation.ok else "error",
                "error_code": observation.error_code,
            }
        )
        result.add_activity(
            f"Tool executed: {observation.tool}",
            "completed" if observation.ok else "error",
            None if observation.ok else observation.error_code,
            observation.tool,
        )

    def _summarize(self, state: AgentExecutionState) -> str:
        """Build the customer reply from verified tool output only."""
        return ResponseBuilder.build(state)

    def _escalation_priority(self, state: AgentExecutionState) -> str:
        """Derive ticket priority from the customer's own signals."""
        text = (state.customer_message or "").lower()
        urgent_words = ("urgent", "asap", "immediately", "right away", "frustrated")
        if any(word in text for word in urgent_words):
            return "high"
        if state.entities.claims_not_received or state.entities.reports_wrong_item:
            return "high"
        return "medium"

    def _escalation_issue(
        self, state: AgentExecutionState, customer_message: str
    ) -> str:
        """Build the ticket body from verified facts only."""
        facts: List[str] = []
        for tool in (
            "get_order",
            "get_delivery_status",
            "get_payment_status",
            "check_refund_status",
            "check_return_eligibility",
        ):
            observation = state.observations.get(tool)
            if observation is None or not observation.ok:
                continue
            data = observation.data or {}
            if tool == "get_order":
                facts.append(f"order {data.get('order_id')} status={data.get('status')}")
            elif tool == "get_delivery_status":
                facts.append(f"delivery status={data.get('status')}")
            elif tool == "get_payment_status":
                facts.append(f"payment status={data.get('status')}")
            elif tool == "check_refund_status":
                facts.append(f"refund status={data.get('status')}")
            elif tool == "check_return_eligibility":
                facts.append(f"return eligible={data.get('eligible')}")

        issue = f"Customer reported: {customer_message.strip()}"
        if state.order_id:
            issue += f" (order {state.order_id})"
        if facts:
            issue += ". Verified by agent: " + "; ".join(facts)
        issue += ". Automatic resolution was not possible; escalated by the support agent."
        return issue

    def _escalate(
        self, result: AgentResult, state: AgentExecutionState, customer_message: str
    ) -> None:
        """Create a real support ticket for a request the agent cannot resolve."""
        customer_id = state.customer_id or state.entities.customer_id
        if not customer_id:
            result.escalated = False
            result.error_code = "MISSING_CUSTOMER_ID"
            result.response = MISSING_INFO_MESSAGES["MISSING_CUSTOMER_ID"]
            result.add_activity(
                "Escalation not possible", "error", "customer_id missing"
            )
            return

        if state.iterations >= self.max_iterations:
            result.escalated = False
            result.error_code = "MAX_ITERATIONS_EXCEEDED"
            result.add_activity(
                "Escalation skipped", "error", f"limit={self.max_iterations}"
            )
            return

        planned = PlannedTool(
            name="create_support_ticket",
            params={
                "customer_id": customer_id,
                "issue": self._escalation_issue(state, customer_message),
                "priority": self._escalation_priority(state),
                "category": state.intent.primary,
            },
            label="Escalate to the support team",
            purpose="create_support_ticket",
        )

        state.iterations += 1
        observation = self.execute_tool(planned)
        self._record_observation(result, observation)
        state.observations[observation.tool] = observation

        if observation.ok and observation.data:
            ticket_id = observation.data.get("ticket_id")
            if ticket_id:
                result.ticket_id = ticket_id
                result.escalated = True
                result.add_activity(
                    "Support ticket created", "completed", ticket_id, "create_support_ticket"
                )
                return
            observation.ok = False
            observation.error_code = "TICKET_NOT_CREATED"
            observation.error_message = "The ticket tool did not return a ticket id."

        result.escalated = False
        result.error_code = observation.error_code or "TICKET_CREATION_FAILED"
        result.add_activity(
            "Ticket creation failed", "error", result.error_code, "create_support_ticket"
        )

    def run(
        self,
        message: Any,
        customer_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        remembered_order_id: Optional[str] = None,
    ) -> AgentResult:
        """Execute one full agent turn: validate, understand, act, observe, decide."""
        result = self.validate(message, customer_id)
        result.conversation_id = conversation_id
        if not result.success:
            result.add_activity(
                "Intent: unknown", "error", result.error_code
            )
            return result

        message_text = result.cleaned_message or ""
        intent = self.detect_intent(message_text)
        intent = self.refine_intent(message_text, intent)
        result.intent = intent.primary
        result.add_activity(f"Intent identified: {intent.primary}")

        entities = self.extract_entities(message_text, result.normalized_customer_id)
        missing, resolved_order_id = self.check_required_info(
            intent, entities, remembered_order_id
        )
        entities.order_id = resolved_order_id

        if missing:
            result.needs_more_info = True
            result.success = False
            result.error_code = (
                "MISSING_ORDER_ID" if "order_id" in missing else "MISSING_CUSTOMER_ID"
            )
            result.response = MISSING_INFO_MESSAGES[result.error_code]
            result.add_activity(
                "Required information missing", "error", ",".join(missing)
            )
            return result

        result.add_activity(
            "Required information available",
            "completed",
            f"order_id={resolved_order_id}" if resolved_order_id else None,
        )

        plan = self.plan_tools(intent, entities)
        if not plan:
            result.add_activity("No tool required for this request")
            result.response = GENERAL_SAFE_RESPONSE
            return result

        state = AgentExecutionState(
            intent=intent,
            entities=entities,
            order_id=resolved_order_id,
            customer_id=result.normalized_customer_id,
            customer_message=message_text,
            plan=plan,
            queue=list(plan),
        )
        result.add_activity(
            "Tool plan created", "completed", ", ".join(p.name for p in plan)
        )

        while state.queue:
            if state.iterations >= self.max_iterations:
                result.error_code = "MAX_ITERATIONS_EXCEEDED"
                result.resolved = False
                result.add_activity(
                    "Iteration limit reached; stopping safely",
                    "error",
                    f"limit={self.max_iterations}",
                )
                break

            planned = state.queue.pop(0)
            state.iterations += 1
            observation = self.execute_tool(planned)
            if planned.name in ORDER_SCOPED_TOOLS:
                observation = self.enforce_ownership(observation, state)

            self._record_observation(result, observation)
            state.executed_tools.append(observation.tool)
            state.observations[observation.tool] = observation

            state.last_decision = self.decide(state, len(state.queue))
            if state.last_decision is not DecisionState.CONTINUE:
                break

        result.observations = state.observations
        result.decision = state.last_decision
        result.response = self._summarize(state)
        self.polish_response(result, state)

        if state.last_decision is DecisionState.COMPLETE:
            result.resolved = True
            result.success = True
            result.add_activity("Verified information gathered; resolution ready")
        elif state.last_decision is DecisionState.ESCALATE:
            result.add_activity(
                "Automatic resolution not possible; escalation required", "error"
            )
            self._escalate(result, state, message_text)
            if result.escalated and result.ticket_id:
                result.success = True
                result.response = (
                    f"I could not resolve this automatically, so I have escalated it to "
                    f"our support team. Your ticket ID is {result.ticket_id}. "
                    f"They will follow up with you shortly."
                )
            elif not result.escalated:
                result.success = False
                result.response = (
                    "I could not resolve this automatically and I was also unable to open "
                    "a support ticket automatically. Please contact our support team so "
                    "they can assist you."
                )
        elif result.error_code != "MAX_ITERATIONS_EXCEEDED":
            result.success = False
            failed = next(
                (o for o in state.observations.values() if not o.ok), None
            )
            if failed is not None:
                result.error_code = failed.error_code
                result.add_activity(
                    "Resolution stopped", "error", failed.error_code
                )

        return result


def run_support_agent(
    message: Any,
    customer_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    remembered_order_id: Optional[str] = None,
    agent: Optional[SupportAgent] = None,
    memory: Optional["AgentMemory"] = None,
    llm: Optional[LLMClient] = None,
) -> AgentOutcome:
    """Run one full agent turn and return the final outcome.

    This is the single entry point the API layer will use. It never raises:
    input problems, guardrail blocks, tool failures and database problems are
    all returned as a structured AgentOutcome.

    Flow: conversation -> context -> agent -> tools -> response -> persist.
    """
    active_agent = agent if agent is not None else SupportAgent()
    active_memory = memory if memory is not None else AgentMemory()

    if llm is None and os.getenv("AGENT_ENABLE_LLM", "").strip().lower() in {"1", "true", "yes"}:
        llm = LLMClient()

    if llm is not None and active_agent.llm is None:
        active_agent.llm = llm

    session_info = active_memory.get_or_create_conversation(customer_id, conversation_id)
    session_error = session_info.get("error_code")

    if session_error == "INVALID_CONVERSATION_ID":
        session_info = active_memory.get_or_create_conversation(customer_id, None)
        session_error = None

    active_conversation_id = session_info.get("conversation_id")

    if session_error and conversation_id:
        logger.info("Conversation %s rejected: %s", conversation_id, session_error)

    context_order_id = None
    if active_conversation_id and not remembered_order_id:
        context_order_id = active_memory.remember_order_id(active_conversation_id)

    try:
        result = active_agent.run(
            message=message,
            customer_id=customer_id,
            conversation_id=active_conversation_id,
            remembered_order_id=remembered_order_id or context_order_id,
        )
    except Exception:
        logger.exception("Unexpected agent failure")
        result = AgentResult(
            success=False,
            response=SAFE_ERROR_MESSAGES["TOOL_FAILURE"],
            error_code="AGENT_FAILURE",
        )
        result.add_activity(
            "Agent stopped safely", "error", "unexpected internal error"
        )
        result.decision = DecisionState.ERROR

    result.conversation_id = active_conversation_id

    if active_conversation_id:
        stored_text = result.cleaned_message or (message if isinstance(message, str) else "")
        active_memory.add_user_message(active_conversation_id, stored_text, result.intent)

        tool_results = {
            tool: (observation.data if observation.ok else {"error_code": observation.error_code})
            for tool, observation in result.observations.items()
        }
        active_memory.add_agent_message(
            active_conversation_id,
            result.response,
            result.intent,
            tool_calls=result.tool_calls,
            tool_results=tool_results,
        )
        active_memory.log_run(active_conversation_id, result)

    if session_error:
        result.add_activity(
            "Conversation memory unavailable", "error", session_error
        )

    return AgentOutcome.from_result(result)