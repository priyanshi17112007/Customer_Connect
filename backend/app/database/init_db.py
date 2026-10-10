from app.database.session import engine, Base
from app.models.models import (
    Customer, Order, Payment, Delivery, Return,
    SupportTicket, TicketMessage, Conversation, Message,
    CustomerStatus, OrderStatus, PaymentStatus, DeliveryStatus,
    ReturnStatus, TicketStatus, TicketPriority
)
import json
from datetime import datetime, timedelta


def init_db():
    Base.metadata.create_all(bind=engine)
    print("Database tables created.")


def seed_data():
    from app.database.session import SessionLocal
    db = SessionLocal()
    
    try:
        # Check if already seeded
        existing = db.query(Customer).first()
        if existing:
            print("Database already seeded.")
            return
        
        # Create customers
        customers = [
            Customer(
                customer_id="CUS-001",
                name="John Smith",
                email="john.smith@email.com",
                phone="+1-555-0101",
                address="123 Main St, New York, NY 10001",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-002",
                name="Sarah Johnson",
                email="sarah.j@email.com",
                phone="+1-555-0102",
                address="456 Oak Ave, Los Angeles, CA 90001",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-003",
                name="Michael Brown",
                email="m.brown@email.com",
                phone="+1-555-0103",
                address="789 Pine Rd, Chicago, IL 60601",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-004",
                name="Emily Davis",
                email="emily.davis@email.com",
                phone="+1-555-0104",
                address="321 Elm St, Houston, TX 77001",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-005",
                name="David Wilson",
                email="d.wilson@email.com",
                phone="+1-555-0105",
                address="654 Maple Dr, Phoenix, AZ 85001",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-006",
                name="Lisa Anderson",
                email="lisa.a@email.com",
                phone="+1-555-0106",
                address="987 Cedar Ln, Philadelphia, PA 19101",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-007",
                name="James Taylor",
                email="j.taylor@email.com",
                phone="+1-555-0107",
                address="147 Birch Blvd, San Antonio, TX 78201",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-008",
                name="Jennifer Martinez",
                email="j.martinez@email.com",
                phone="+1-555-0108",
                address="258 Spruce Ct, San Diego, CA 92101",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-009",
                name="Robert Garcia",
                email="r.garcia@email.com",
                phone="+1-555-0109",
                address="369 Willow Way, Dallas, TX 75201",
                status=CustomerStatus.ACTIVE
            ),
            Customer(
                customer_id="CUS-010",
                name="Amanda Robinson",
                email="amanda.r@email.com",
                phone="+1-555-0110",
                address="741 Ash Pl, San Jose, CA 95101",
                status=CustomerStatus.ACTIVE
            ),
        ]
        
        for c in customers:
            db.add(c)
        db.commit()
        
        # Refresh to get IDs
        for c in customers:
            db.refresh(c)
        
        # Create orders
        orders = [
            Order(
                order_id="ORD-10234",
                customer_id=customers[0].id,
                status=OrderStatus.OUT_FOR_DELIVERY,
                items=json.dumps([{"name": "Laptop Stand", "quantity": 1, "price": 49.99}, {"name": "Wireless Mouse", "quantity": 1, "price": 29.99}]),
                total_amount=79.98,
                created_at=datetime.now() - timedelta(days=5)
            ),
            Order(
                order_id="ORD-10235",
                customer_id=customers[0].id,
                status=OrderStatus.DELIVERED,
                items=json.dumps([{"name": "USB-C Hub", "quantity": 1, "price": 79.99}]),
                total_amount=79.99,
                created_at=datetime.now() - timedelta(days=10)
            ),
            Order(
                order_id="ORD-10236",
                customer_id=customers[1].id,
                status=OrderStatus.SHIPPED,
                items=json.dumps([{"name": "Mechanical Keyboard", "quantity": 1, "price": 149.99}]),
                total_amount=149.99,
                created_at=datetime.now() - timedelta(days=3)
            ),
            Order(
                order_id="ORD-10237",
                customer_id=customers[1].id,
                status=OrderStatus.CANCELLED,
                items=json.dumps([{"name": "Monitor 27\"", "quantity": 1, "price": 299.99}]),
                total_amount=299.99,
                created_at=datetime.now() - timedelta(days=15)
            ),
            Order(
                order_id="ORD-10238",
                customer_id=customers[2].id,
                status=OrderStatus.DELIVERED,
                items=json.dumps([{"name": "Webcam HD", "quantity": 1, "price": 89.99}, {"name": "Ring Light", "quantity": 1, "price": 45.99}]),
                total_amount=135.98,
                created_at=datetime.now() - timedelta(days=20)
            ),
            Order(
                order_id="ORD-10239",
                customer_id=customers[3].id,
                status=OrderStatus.PROCESSING,
                items=json.dumps([{"name": "External SSD 1TB", "quantity": 1, "price": 129.99}]),
                total_amount=129.99,
                created_at=datetime.now() - timedelta(days=1)
            ),
            Order(
                order_id="ORD-10240",
                customer_id=customers[3].id,
                status=OrderStatus.RETURNED,
                items=json.dumps([{"name": "Headphones", "quantity": 1, "price": 199.99}]),
                total_amount=199.99,
                created_at=datetime.now() - timedelta(days=30)
            ),
            Order(
                order_id="ORD-10241",
                customer_id=customers[4].id,
                status=OrderStatus.SHIPPED,
                items=json.dumps([{"name": "Tablet Stand", "quantity": 2, "price": 24.99}]),
                total_amount=49.98,
                created_at=datetime.now() - timedelta(days=2)
            ),
            Order(
                order_id="ORD-10242",
                customer_id=customers[5].id,
                status=OrderStatus.DELIVERED,
                items=json.dumps([{"name": "Phone Case", "quantity": 1, "price": 19.99}, {"name": "Screen Protector", "quantity": 2, "price": 9.99}]),
                total_amount=39.97,
                created_at=datetime.now() - timedelta(days=7)
            ),
            Order(
                order_id="ORD-10243",
                customer_id=customers[6].id,
                status=OrderStatus.CANCELLED,
                items=json.dumps([{"name": "Smart Watch", "quantity": 1, "price": 249.99}]),
                total_amount=249.99,
                created_at=datetime.now() - timedelta(days=12)
            ),
        ]
        
        for o in orders:
            db.add(o)
        db.commit()
        
        for o in orders:
            db.refresh(o)
        
        # Create payments
        payments = [
            Payment(
                payment_id="PAY-5001",
                order_id=orders[0].id,
                status=PaymentStatus.COMPLETED,
                amount=79.98,
                payment_method="Credit Card",
                transaction_id="TXN-10001",
                created_at=orders[0].created_at
            ),
            Payment(
                payment_id="PAY-5002",
                order_id=orders[1].id,
                status=PaymentStatus.COMPLETED,
                amount=79.99,
                payment_method="PayPal",
                transaction_id="TXN-10002",
                created_at=orders[1].created_at
            ),
            Payment(
                payment_id="PAY-5003",
                order_id=orders[2].id,
                status=PaymentStatus.COMPLETED,
                amount=149.99,
                payment_method="Credit Card",
                transaction_id="TXN-10003",
                created_at=orders[2].created_at
            ),
            Payment(
                payment_id="PAY-5004",
                order_id=orders[3].id,
                status=PaymentStatus.REFUNDED,
                amount=299.99,
                payment_method="Credit Card",
                transaction_id="TXN-10004",
                created_at=orders[3].created_at
            ),
            Payment(
                payment_id="PAY-5005",
                order_id=orders[4].id,
                status=PaymentStatus.COMPLETED,
                amount=135.98,
                payment_method="Debit Card",
                transaction_id="TXN-10005",
                created_at=orders[4].created_at
            ),
            Payment(
                payment_id="PAY-5006",
                order_id=orders[5].id,
                status=PaymentStatus.PENDING,
                amount=129.99,
                payment_method="Credit Card",
                transaction_id="TXN-10006",
                created_at=orders[5].created_at
            ),
            Payment(
                payment_id="PAY-5007",
                order_id=orders[6].id,
                status=PaymentStatus.REFUNDED,
                amount=199.99,
                payment_method="PayPal",
                transaction_id="TXN-10007",
                created_at=orders[6].created_at
            ),
            Payment(
                payment_id="PAY-5008",
                order_id=orders[7].id,
                status=PaymentStatus.COMPLETED,
                amount=49.98,
                payment_method="Credit Card",
                transaction_id="TXN-10008",
                created_at=orders[7].created_at
            ),
            Payment(
                payment_id="PAY-5009",
                order_id=orders[8].id,
                status=PaymentStatus.COMPLETED,
                amount=39.97,
                payment_method="Apple Pay",
                transaction_id="TXN-10009",
                created_at=orders[8].created_at
            ),
            Payment(
                payment_id="PAY-5010",
                order_id=orders[9].id,
                status=PaymentStatus.REFUNDED,
                amount=249.99,
                payment_method="Credit Card",
                transaction_id="TXN-10010",
                created_at=orders[9].created_at
            ),
        ]
        
        for p in payments:
            db.add(p)
        db.commit()
        
        # Create deliveries
        deliveries = [
            Delivery(
                delivery_id="DEL-2001",
                order_id=orders[0].id,
                status=DeliveryStatus.OUT_FOR_DELIVERY,
                carrier="FedEx",
                tracking_number="FDX123456789",
                estimated_delivery=datetime.now() + timedelta(hours=4),
                delivery_address=customers[0].address,
                created_at=orders[0].created_at + timedelta(days=3)
            ),
            Delivery(
                delivery_id="DEL-2002",
                order_id=orders[1].id,
                status=DeliveryStatus.DELIVERED,
                carrier="UPS",
                tracking_number="UPS987654321",
                estimated_delivery=orders[1].created_at + timedelta(days=5),
                actual_delivery=orders[1].created_at + timedelta(days=5),
                delivery_address=customers[0].address,
                created_at=orders[1].created_at + timedelta(days=2)
            ),
            Delivery(
                delivery_id="DEL-2003",
                order_id=orders[2].id,
                status=DeliveryStatus.SHIPPED,
                carrier="DHL",
                tracking_number="DHL456789123",
                estimated_delivery=datetime.now() + timedelta(days=2),
                delivery_address=customers[1].address,
                created_at=orders[2].created_at + timedelta(days=1)
            ),
            Delivery(
                delivery_id="DEL-2004",
                order_id=orders[3].id,
                status=DeliveryStatus.RETURNED,
                carrier="FedEx",
                tracking_number="FDX111222333",
                estimated_delivery=orders[3].created_at + timedelta(days=3),
                actual_delivery=orders[3].created_at + timedelta(days=3),
                delivery_address=customers[1].address,
                created_at=orders[3].created_at + timedelta(days=1)
            ),
            Delivery(
                delivery_id="DEL-2005",
                order_id=orders[4].id,
                status=DeliveryStatus.DELIVERED,
                carrier="UPS",
                tracking_number="UPS555666777",
                estimated_delivery=orders[4].created_at + timedelta(days=4),
                actual_delivery=orders[4].created_at + timedelta(days=4),
                delivery_address=customers[2].address,
                created_at=orders[4].created_at + timedelta(days=1)
            ),
            Delivery(
                delivery_id="DEL-2006",
                order_id=orders[5].id,
                status=DeliveryStatus.PENDING,
                carrier="FedEx",
                tracking_number="FDX999888777",
                estimated_delivery=datetime.now() + timedelta(days=3),
                delivery_address=customers[3].address,
                created_at=orders[5].created_at
            ),
            Delivery(
                delivery_id="DEL-2007",
                order_id=orders[6].id,
                status=DeliveryStatus.DELIVERED,
                carrier="DHL",
                tracking_number="DHL111222333",
                estimated_delivery=orders[6].created_at + timedelta(days=5),
                actual_delivery=orders[6].created_at + timedelta(days=5),
                delivery_address=customers[3].address,
                created_at=orders[6].created_at + timedelta(days=2)
            ),
            Delivery(
                delivery_id="DEL-2008",
                order_id=orders[7].id,
                status=DeliveryStatus.SHIPPED,
                carrier="UPS",
                tracking_number="UPS444555666",
                estimated_delivery=datetime.now() + timedelta(days=1),
                delivery_address=customers[4].address,
                created_at=orders[7].created_at + timedelta(days=1)
            ),
            Delivery(
                delivery_id="DEL-2009",
                order_id=orders[8].id,
                status=DeliveryStatus.DELIVERED,
                carrier="FedEx",
                tracking_number="FDX777888999",
                estimated_delivery=orders[8].created_at + timedelta(days=3),
                actual_delivery=orders[8].created_at + timedelta(days=3),
                delivery_address=customers[5].address,
                created_at=orders[8].created_at + timedelta(days=1)
            ),
            Delivery(
                delivery_id="DEL-2010",
                order_id=orders[9].id,
                status=DeliveryStatus.RETURNED,
                carrier="UPS",
                tracking_number="UPS123123123",
                estimated_delivery=orders[9].created_at + timedelta(days=4),
                actual_delivery=orders[9].created_at + timedelta(days=4),
                delivery_address=customers[6].address,
                created_at=orders[9].created_at + timedelta(days=1)
            ),
        ]
        
        for d in deliveries:
            db.add(d)
        db.commit()
        
        # Create returns
        returns = [
            Return(
                return_id="RET-3001",
                order_id=orders[6].id,
                status=ReturnStatus.PROCESSED,
                reason="Wrong item received",
                items=json.dumps([{"name": "Headphones", "quantity": 1, "price": 199.99}]),
                refund_amount=199.99,
                created_at=orders[6].created_at + timedelta(days=10)
            ),
            Return(
                return_id="RET-3002",
                order_id=orders[9].id,
                status=ReturnStatus.RECEIVED,
                reason="Changed mind",
                items=json.dumps([{"name": "Smart Watch", "quantity": 1, "price": 249.99}]),
                refund_amount=249.99,
                created_at=orders[9].created_at + timedelta(days=5)
            ),
        ]
        
        for r in returns:
            db.add(r)
        db.commit()
        
        # Create support tickets
        tickets = [
            SupportTicket(
                ticket_id="TKT-10001",
                customer_id=customers[0].id,
                subject="Order ORD-10235 delivered but item missing",
                description="My order ORD-10235 was marked as delivered but I only received the USB-C Hub, missing the laptop sleeve that was supposed to be included.",
                status=TicketStatus.IN_PROGRESS,
                priority=TicketPriority.MEDIUM,
                category="Delivery",
                assigned_to="Support Agent 1",
                created_at=datetime.now() - timedelta(days=2)
            ),
            SupportTicket(
                ticket_id="TKT-10002",
                customer_id=customers[2].id,
                subject="Refund not received for cancelled order ORD-10237",
                description="Order ORD-10237 was cancelled but I haven't received the refund yet. It's been 15 days.",
                status=TicketStatus.OPEN,
                priority=TicketPriority.HIGH,
                category="Refund",
                created_at=datetime.now() - timedelta(days=1)
            ),
            SupportTicket(
                ticket_id="TKT-10003",
                customer_id=customers[4].id,
                subject="Cannot login to account",
                description="I'm unable to login to my account. Password reset emails are not arriving.",
                status=TicketStatus.RESOLVED,
                priority=TicketPriority.LOW,
                category="Account",
                assigned_to="Support Agent 2",
                resolution="Password reset link sent to registered email. Issue resolved.",
                created_at=datetime.now() - timedelta(days=5),
                resolved_at=datetime.now() - timedelta(days=3)
            ),
        ]
        
        for t in tickets:
            db.add(t)
        db.commit()
        
        for t in tickets:
            db.refresh(t)
        
        # Create ticket messages
        ticket_messages = [
            TicketMessage(
                ticket_id=tickets[0].id,
                sender_type="customer",
                sender_id=customers[0].customer_id,
                message="My order ORD-10235 was marked as delivered but I only received the USB-C Hub, missing the laptop sleeve that was supposed to be included.",
                created_at=datetime.now() - timedelta(days=2)
            ),
            TicketMessage(
                ticket_id=tickets[0].id,
                sender_type="agent",
                sender_id="Support Agent 1",
                message="I apologize for the missing item. Let me check the order details and investigate with the warehouse.",
                created_at=datetime.now() - timedelta(days=2, hours=2)
            ),
            TicketMessage(
                ticket_id=tickets[1].id,
                sender_type="customer",
                sender_id=customers[2].customer_id,
                message="Order ORD-10237 was cancelled but I haven't received the refund yet. It's been 15 days.",
                created_at=datetime.now() - timedelta(days=1)
            ),
        ]
        
        for tm in ticket_messages:
            db.add(tm)
        db.commit()
        
        # Create conversations
        conversations = [
            Conversation(
                conversation_id="CONV-001",
                customer_id=customers[0].id,
                status="active",
                created_at=datetime.now() - timedelta(days=1)
            ),
            Conversation(
                conversation_id="CONV-002",
                customer_id=customers[1].id,
                status="active",
                created_at=datetime.now() - timedelta(hours=5)
            ),
        ]
        
        for c in conversations:
            db.add(c)
        db.commit()
        
        for c in conversations:
            db.refresh(c)
        
        # Create messages
        messages = [
            Message(
                conversation_id=conversations[0].id,
                role="user",
                content="Where is my order ORD-10234?",
                intent="delivery_status",
                created_at=datetime.now() - timedelta(days=1)
            ),
            Message(
                conversation_id=conversations[0].id,
                role="assistant",
                content="Your order ORD-10234 is currently out for delivery. Estimated delivery is today.",
                intent="delivery_status",
                tool_calls=json.dumps([{"tool": "get_order", "input": {"order_id": "ORD-10234"}}, {"tool": "get_delivery_status", "input": {"order_id": "ORD-10234"}}]),
                tool_results=json.dumps([{"order_id": "ORD-10234", "status": "out_for_delivery"}, {"status": "out_for_delivery", "estimated_delivery": "2026-10-04"}]),
                created_at=datetime.now() - timedelta(days=1, hours=-1)
            ),
            Message(
                conversation_id=conversations[1].id,
                role="user",
                content="What's the status of my payment for ORD-10236?",
                intent="payment_status",
                created_at=datetime.now() - timedelta(hours=5)
            ),
            Message(
                conversation_id=conversations[1].id,
                role="assistant",
                content="Your payment for order ORD-10236 has been completed successfully.",
                intent="payment_status",
                tool_calls=json.dumps([{"tool": "get_order", "input": {"order_id": "ORD-10236"}}, {"tool": "get_payment_status", "input": {"order_id": "ORD-10236"}}]),
                tool_results=json.dumps([{"order_id": "ORD-10236", "status": "shipped"}, {"status": "completed", "amount": 149.99}]),
                created_at=datetime.now() - timedelta(hours=4, minutes=55)
            ),
        ]
        
        for m in messages:
            db.add(m)
        db.commit()
        
        print("Database seeded successfully!")
        
    except Exception as e:
        print(f"Error seeding database: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    seed_data()