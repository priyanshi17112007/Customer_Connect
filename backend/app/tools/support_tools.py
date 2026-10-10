from sqlalchemy.orm import Session
from app.models.models import (
    Order, Payment, Delivery, Return, Customer, SupportTicket,
    OrderStatus, DeliveryStatus, TicketStatus, TicketPriority,
)
from app.database.session import SessionLocal
from typing import Dict, Any
import json
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.security.redaction import redact

logger = logging.getLogger(__name__)

# Ticket identifiers keep the historical TKT-<5 digits> shape. The space is small
# enough that collisions eventually happen, so creation is retry-based: the UNIQUE
# constraint is the authority on whether an id is free.
TICKET_ID_MIN = 10000
TICKET_ID_MAX = 99999
TICKET_ID_ATTEMPTS = 8


class ToolError(Exception):
    """Custom exception for tool errors."""
    def __init__(self, message: str, error_code: str = "TOOL_ERROR"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


def _new_ticket_id() -> str:
    """Draw a fresh candidate ticket id."""
    return f"TKT-{secrets.randbelow(TICKET_ID_MAX - TICKET_ID_MIN + 1) + TICKET_ID_MIN}"


def _is_ticket_id_conflict(exc: BaseException) -> bool:
    """True when an IntegrityError was caused by the ticket-id unique index.

    Any other integrity failure is re-raised untouched so a genuine schema
    problem still surfaces as DATABASE_ERROR instead of being retried away.
    """
    if not isinstance(exc, IntegrityError):
        return False
    message = str(getattr(exc, "orig", exc))
    return "ticket_id" in message


def get_db_session() -> Session:
    """Get a database session."""
    return SessionLocal()


def get_order(order_id: str) -> Dict[str, Any]:
    """Retrieve order information by order ID."""
    db = get_db_session()
    try:
        order = db.query(Order).filter(Order.order_id == order_id).first()
        if not order:
            raise ToolError(f"Order {order_id} not found", "INVALID_ORDER_ID")
        
        items = json.loads(order.items) if order.items else []
        
        return {
            "order_id": order.order_id,
            "customer_id": order.customer.customer_id if order.customer else None,
            "status": order.status.value,
            "items": items,
            "total": order.total_amount,
            "currency": order.currency,
            "created_at": order.created_at.isoformat() if order.created_at else None
        }
    except ToolError:
        raise
    except Exception as e:
        logger.error(redact(f"Error retrieving order {e}: {e}"))
        raise ToolError("Failed to retrieve order information", "DATABASE_ERROR")
    finally:
        db.close()


def get_customer(customer_id: str) -> Dict[str, Any]:
    """Retrieve customer information by customer ID."""
    db = get_db_session()
    try:
        customer = db.query(Customer).filter(Customer.customer_id == customer_id).first()
        if not customer:
            raise ToolError(f"Customer {customer_id} not found", "INVALID_CUSTOMER_ID")
        
        # Get recent orders
        recent_orders = db.query(Order).filter(Order.customer_id == customer.id).order_by(Order.created_at.desc()).limit(5).all()
        orders_data = [
            {
                "order_id": o.order_id,
                "status": o.status.value,
                "total": o.total_amount,
                "created_at": o.created_at.isoformat() if o.created_at else None
            }
            for o in recent_orders
        ]
        
        # Get open tickets
        open_tickets = db.query(SupportTicket).filter(
            SupportTicket.customer_id == customer.id,
            SupportTicket.status.in_(["open", "in_progress", "pending_customer"])
        ).all()
        tickets_data = [
            {
                "ticket_id": t.ticket_id,
                "subject": t.subject,
                "status": t.status.value,
                "priority": t.priority.value,
                "created_at": t.created_at.isoformat() if t.created_at else None
            }
            for t in open_tickets
        ]
        
        return {
            "customer_id": customer.customer_id,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "address": customer.address,
            "status": customer.status.value,
            "recent_orders": orders_data,
            "open_tickets": tickets_data
        }
    except ToolError:
        raise
    except Exception as e:
        logger.error(redact(f"Error retrieving customer {e}: {e}"))
        raise ToolError("Failed to retrieve customer information", "DATABASE_ERROR")
    finally:
        db.close()


def get_delivery_status(order_id: str) -> Dict[str, Any]:
    """Retrieve delivery status for an order."""
    db = get_db_session()
    try:
        order = db.query(Order).filter(Order.order_id == order_id).first()
        if not order:
            raise ToolError(f"Order {order_id} not found", "INVALID_ORDER_ID")
        
        delivery = db.query(Delivery).filter(Delivery.order_id == order.id).first()
        if not delivery:
            return {
                "order_id": order_id,
                "delivery_id": None,
                "status": "no_delivery_info",
                "carrier": None,
                "tracking_number": None,
                "estimated_delivery": None,
                "actual_delivery": None,
                "message": "No delivery information available for this order"
            }
        
        return {
            "order_id": order_id,
            "delivery_id": delivery.delivery_id,
            "status": delivery.status.value,
            "carrier": delivery.carrier,
            "tracking_number": delivery.tracking_number,
            "estimated_delivery": delivery.estimated_delivery.isoformat() if delivery.estimated_delivery else None,
            "actual_delivery": delivery.actual_delivery.isoformat() if delivery.actual_delivery else None
        }
    except ToolError:
        raise
    except Exception as e:
        logger.error(redact(f"Error retrieving delivery for order {e}: {e}"))
        raise ToolError("Failed to retrieve delivery information", "DATABASE_ERROR")
    finally:
        db.close()


def get_payment_status(order_id: str) -> Dict[str, Any]:
    """Retrieve payment status for an order."""
    db = get_db_session()
    try:
        order = db.query(Order).filter(Order.order_id == order_id).first()
        if not order:
            raise ToolError(f"Order {order_id} not found", "INVALID_ORDER_ID")
        
        payment = db.query(Payment).filter(Payment.order_id == order.id).first()
        if not payment:
            return {
                "order_id": order_id,
                "payment_id": None,
                "status": "no_payment_info",
                "amount": 0.0,
                "currency": "USD",
                "payment_method": None,
                "transaction_id": None,
                "message": "No payment information available for this order"
            }
        
        return {
            "order_id": order_id,
            "payment_id": payment.payment_id,
            "status": payment.status.value,
            "amount": payment.amount,
            "currency": payment.currency,
            "payment_method": payment.payment_method,
            "transaction_id": payment.transaction_id
        }
    except ToolError:
        raise
    except Exception as e:
        logger.error(redact(f"Error retrieving payment for order {e}: {e}"))
        raise ToolError("Failed to retrieve payment information", "DATABASE_ERROR")
    finally:
        db.close()


def check_refund_status(order_id: str) -> Dict[str, Any]:
    """Check refund status for an order."""
    db = get_db_session()
    try:
        order = db.query(Order).filter(Order.order_id == order_id).first()
        if not order:
            raise ToolError(f"Order {order_id} not found", "INVALID_ORDER_ID")
        
        # Check payment for refund status
        payment = db.query(Payment).filter(Payment.order_id == order.id).first()
        
        # Check returns for refund info
        return_record = db.query(Return).filter(Return.order_id == order.id).first()
        
        if not payment and not return_record:
            return {
                "order_id": order_id,
                "refund_id": None,
                "status": "no_refund_info",
                "amount": 0.0,
                "reason": None,
                "message": "No refund information available for this order"
            }
        
        refund_status = "none"
        refund_amount = 0.0
        refund_reason = None
        refund_id = None
        
        if payment and payment.status.value == "refunded":
            refund_status = "refunded"
            refund_amount = payment.amount
            refund_id = payment.payment_id
        elif return_record:
            if return_record.status.value in ["processed", "received"]:
                refund_status = "processed" if return_record.status.value == "processed" else "processing"
                refund_amount = return_record.refund_amount
                refund_reason = return_record.reason
                refund_id = return_record.return_id
            elif return_record.status.value == "approved":
                refund_status = "approved"
                refund_amount = return_record.refund_amount
                refund_reason = return_record.reason
                refund_id = return_record.return_id
            elif return_record.status.value == "requested":
                refund_status = "requested"
                refund_reason = return_record.reason
                refund_id = return_record.return_id
        
        return {
            "order_id": order_id,
            "refund_id": refund_id,
            "status": refund_status,
            "amount": refund_amount,
            "reason": refund_reason
        }
    except ToolError:
        raise
    except Exception as e:
        logger.error(redact(f"Error checking refund for order {e}: {e}"))
        raise ToolError("Failed to check refund status", "DATABASE_ERROR")
    finally:
        db.close()


def check_return_eligibility(order_id: str) -> Dict[str, Any]:
    """Check if an order is eligible for return."""
    db = get_db_session()
    try:
        order = db.query(Order).filter(Order.order_id == order_id).first()
        if not order:
            raise ToolError(f"Order {order_id} not found", "INVALID_ORDER_ID")
        
        # Check if already returned
        existing_return = db.query(Return).filter(Return.order_id == order.id).first()
        if existing_return:
            return {
                "order_id": order_id,
                "eligible": False,
                "reason": f"Return already exists with status: {existing_return.status.value}",
                "return_window_days": None,
                "return_id": existing_return.return_id
            }
        
        # Check order status for eligibility
        non_returnable_statuses = [OrderStatus.CANCELLED, OrderStatus.RETURNED, OrderStatus.REFUNDED]
        if order.status in non_returnable_statuses:
            return {
                "order_id": order_id,
                "eligible": False,
                "reason": f"Order status '{order.status.value}' is not eligible for return",
                "return_window_days": None,
                "return_id": None
            }
        
        # Check delivery status - must be delivered
        delivery = db.query(Delivery).filter(Delivery.order_id == order.id).first()
        if not delivery or delivery.status != DeliveryStatus.DELIVERED:
            return {
                "order_id": order_id,
                "eligible": False,
                "reason": "Order must be delivered before it can be returned",
                "return_window_days": None,
                "return_id": None
            }
        
        # Check return window (30 days from delivery)
        if delivery.actual_delivery:
            # The column is timezone-aware. SQLite hands back naive datetimes
            # while PostgreSQL hands back aware ones, so normalise both sides to
            # naive UTC before subtracting; comparing them directly raises
            # TypeError on any timezone-aware backend.
            delivered = delivery.actual_delivery
            if delivered.tzinfo is not None:
                delivered = delivered.astimezone(timezone.utc).replace(tzinfo=None)
            days_since_delivery = (datetime.now(timezone.utc).replace(tzinfo=None) - delivered).days
            if days_since_delivery > 30:
                return {
                    "order_id": order_id,
                    "eligible": False,
                    "reason": f"Return window expired. {days_since_delivery} days since delivery (30 day limit)",
                    "return_window_days": 30,
                    "return_id": None
                }
            return_window_remaining = 30 - days_since_delivery
        else:
            return_window_remaining = 30
        
        return {
            "order_id": order_id,
            "eligible": True,
            "reason": "Order is eligible for return",
            "return_window_days": return_window_remaining,
            "return_id": None
        }
    except ToolError:
        raise
    except Exception as e:
        logger.error(redact(f"Error checking return eligibility for order {e}: {e}"))
        raise ToolError("Failed to check return eligibility", "DATABASE_ERROR")
    finally:
        db.close()


def create_support_ticket(customer_id: str, issue: str, priority: str = "medium", category: str = "General") -> Dict[str, Any]:
    """Create a support ticket for escalation.

    A ticket-id collision on the UNIQUE index is retried with a fresh id up to
    TICKET_ID_ATTEMPTS times. An existing ticket is never overwritten. Any other
    integrity failure, or an exhausted retry budget, becomes DATABASE_ERROR.
    """
    db = get_db_session()
    try:
        customer = db.query(Customer).filter(Customer.customer_id == customer_id).first()
        if not customer:
            raise ToolError(f"Customer {customer_id} not found", "INVALID_CUSTOMER_ID")

        # Read the primary key once, as a plain value. A retry rolls back and
        # detaches every ORM instance in the session, so the loop below must not
        # touch ORM attributes to find the owner.
        customer_pk = customer.id

        # Generate a candidate ticket ID. The insert below is the authority: a
        # candidate that is already taken raises the UNIQUE constraint and the
        # caller retries with a new one, so an existing ticket is never
        # overwritten and never silently reused.
        ticket_id = _new_ticket_id()

        # Validate priority
        try:
            priority_enum = TicketPriority(str(priority).lower())
        except ValueError:
            priority_enum = TicketPriority.MEDIUM

        for attempt in range(1, TICKET_ID_ATTEMPTS + 1):
            ticket = SupportTicket(
                ticket_id=ticket_id,
                customer_id=customer_pk,
                subject=issue[:200],  # Truncate if too long
                description=issue,
                priority=priority_enum,
                category=category,
                status=TicketStatus.OPEN
            )

            db.add(ticket)
            try:
                db.commit()
            except IntegrityError as exc:
                # Drop the failed unit of work and the pending row before
                # retrying, so the next attempt starts from a clean session.
                db.rollback()
                db.expunge_all()
                if not _is_ticket_id_conflict(exc):
                    raise
                if attempt == TICKET_ID_ATTEMPTS:
                    logger.error(
                        "Support ticket id collision %s times; giving up",
                        TICKET_ID_ATTEMPTS,
                    )
                    raise ToolError("Failed to create support ticket", "DATABASE_ERROR")
                logger.warning(
                    "Support ticket id already in use, retrying (%s/%s)",
                    attempt,
                    TICKET_ID_ATTEMPTS,
                )
                ticket_id = _new_ticket_id()
                continue
            db.refresh(ticket)

            return {
                "ticket_id": ticket.ticket_id,
                "customer_id": customer_id,
                "subject": ticket.subject,
                "description": ticket.description,
                "status": ticket.status.value,
                "priority": ticket.priority.value,
                "category": ticket.category,
                "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
                "message": f"Support ticket {ticket_id} created successfully"
            }
    except ToolError:
        raise
    except Exception as e:
        logger.error(redact(f"Error creating support ticket for customer {e}: {e}"))
        db.rollback()
        raise ToolError("Failed to create support ticket", "DATABASE_ERROR")
    finally:
        db.close()


# Tool registry for the agent
TOOLS = {
    "get_order": {
        "function": get_order,
        "description": "Retrieve order information by order ID",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        "risk_level": "low"
    },
    "get_customer": {
        "function": get_customer,
        "description": "Retrieve customer information by customer ID",
        "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
        "risk_level": "low"
    },
    "get_delivery_status": {
        "function": get_delivery_status,
        "description": "Check delivery status for an order",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        "risk_level": "low"
    },
    "get_payment_status": {
        "function": get_payment_status,
        "description": "Check payment status for an order",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        "risk_level": "low"
    },
    "check_refund_status": {
        "function": check_refund_status,
        "description": "Check refund status for an order",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        "risk_level": "medium"
    },
    "check_return_eligibility": {
        "function": check_return_eligibility,
        "description": "Check if an order is eligible for return",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        "risk_level": "medium"
    },
    "create_support_ticket": {
        "function": create_support_ticket,
        "description": "Create a support ticket for escalation",
        "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}, "issue": {"type": "string"}, "priority": {"type": "string"}, "category": {"type": "string"}}, "required": ["customer_id", "issue"]},
        "risk_level": "medium"
    },
}