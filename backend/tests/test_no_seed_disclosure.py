"""No seeded identifier may appear in generic or example response content.

The live acceptance test found that the missing-information replies carried a
hardcoded seed order and customer ("it looks like ORD-10234"). Those messages are
sent to *any* caller, including one who owns no orders, so they disclosed another
account's identifiers and asserted a fact that no tool had verified - a direct
contradiction of the project's no-fabrication policy.

These tests pin the fix from both directions: the generic messages must stay free
of concrete identifiers, and genuinely verified order IDs must still reach the
customer normally. The over-correction (stripping real IDs everywhere) would be
just as wrong as the original defect.
"""

import re
import unittest

from .helpers import auth_headers, get_client, reset_counters

# A concrete identifier: real digits after the prefix.
CONCRETE_ORDER = re.compile(r"\bORD-\d+\b")
CONCRETE_CUSTOMER = re.compile(r"\bCUS-\d+\b")

# Seeded range, used to assert nothing seeded is quoted back.
SEEDED_ORDERS = {f"ORD-{n}" for n in range(10234, 10244)}
SEEDED_CUSTOMERS = {f"CUS-{n:03d}" for n in range(1, 11)}


def assertNoConcreteIdentifiers(case, text, label):
    case.assertIsNotNone(text, label)
    case.assertIsNone(CONCRETE_ORDER.search(text), f"{label} leaked an order id: {text!r}")
    case.assertIsNone(
        CONCRETE_CUSTOMER.search(text), f"{label} leaked a customer id: {text!r}"
    )


class TestGenericMessagesCarryNoSeedIdentifiers(unittest.TestCase):
    def test_missing_order_id_response_names_no_concrete_order(self):
        """The live defect: CUS-003 was told 'it looks like ORD-10234'."""
        from app.agents.support_agent import MISSING_INFO_MESSAGES

        text = MISSING_INFO_MESSAGES["MISSING_ORDER_ID"]
        assertNoConcreteIdentifiers(self, text, "MISSING_ORDER_ID")
        self.assertNotIn("ORD-10234", text)

    def test_missing_customer_id_response_names_no_concrete_customer(self):
        from app.agents.support_agent import MISSING_INFO_MESSAGES

        text = MISSING_INFO_MESSAGES["MISSING_CUSTOMER_ID"]
        assertNoConcreteIdentifiers(self, text, "MISSING_CUSTOMER_ID")
        self.assertNotIn("CUS-001", text)

    def test_every_missing_info_message_is_identifier_free(self):
        from app.agents.support_agent import MISSING_INFO_MESSAGES

        for code, text in MISSING_INFO_MESSAGES.items():
            assertNoConcreteIdentifiers(self, text, f"MISSING_INFO_MESSAGES[{code!r}]")

    def test_invalid_customer_id_validation_message_is_identifier_free(self):
        """The validator path, which HTTP cannot reach (Pydantic rejects first)."""
        from app.agents.support_agent import SupportAgent

        result = SupportAgent().validate("Where is my order?", "not-a-customer-id")
        self.assertEqual("INVALID_CUSTOMER_ID", result.error_code)
        assertNoConcreteIdentifiers(self, result.response, "INVALID_CUSTOMER_ID")
        self.assertIn("CUS-", result.response, "must still state the expected format")

    def test_generic_format_hints_are_kept(self):
        """The messages must stay useful, not just be emptied of identifiers."""
        from app.agents.support_agent import MISSING_INFO_MESSAGES

        self.assertIn("ORD-", MISSING_INFO_MESSAGES["MISSING_ORDER_ID"])
        self.assertIn("CUS-", MISSING_INFO_MESSAGES["MISSING_CUSTOMER_ID"])


class TestNoIdentifierLeaksOverTheWire(unittest.TestCase):
    def setUp(self):
        self.client = get_client()
        reset_counters()

    def chat(self, customer, message):
        res = self.client.post(
            "/api/v1/chat",
            json={"customer_id": customer, "message": message},
            headers=auth_headers(),
        )
        self.assertEqual(200, res.status_code)
        return res.json()["data"]

    def test_missing_order_id_reply_over_http_is_identifier_free(self):
        data = self.chat("CUS-003", "What is the status of my order?")
        self.assertEqual("MISSING_ORDER_ID", data["error_code"])
        assertNoConcreteIdentifiers(self, data["response"], "MISSING_ORDER_ID over HTTP")
        # Nothing was verified, so no tool may have run either.
        self.assertEqual([], data["tool_calls"])

    def test_missing_order_id_reply_is_safe_for_a_customer_with_no_orders(self):
        data = self.chat("CUS-007", "Can I return my order?")
        assertNoConcreteIdentifiers(self, data["response"], "CUS-007 reply")

    def test_public_openapi_schema_exposes_no_seeded_identifier(self):
        """/openapi.json is unauthenticated, so its examples are public too."""
        res = self.client.get("/openapi.json")
        self.assertEqual(200, res.status_code)
        body = res.text
        assertNoConcreteIdentifiers(self, body, "openapi.json")
        for seeded in SEEDED_CUSTOMERS:
            self.assertNotIn(seeded, body)

    def test_denied_cross_customer_reply_still_names_no_order(self):
        data = self.chat("CUS-001", "What is the status of my order ORD-10236?")
        self.assertEqual("ORDER_NOT_ACCESSIBLE", data["error_code"])
        assertNoConcreteIdentifiers(self, data["response"], "denied reply")


class TestVerifiedIdentifiersStillReachTheCustomer(unittest.TestCase):
    """Requirement 13: the fix must not suppress genuinely verified order IDs."""

    def setUp(self):
        self.client = get_client()
        reset_counters()

    def chat(self, customer, message):
        res = self.client.post(
            "/api/v1/chat",
            json={"customer_id": customer, "message": message},
            headers=auth_headers(),
        )
        self.assertEqual(200, res.status_code)
        return res.json()["data"]

    def test_own_order_id_is_still_reported(self):
        data = self.chat("CUS-001", "What is the status of my order ORD-10234?")
        self.assertIsNone(data["error_code"])
        self.assertIn("ORD-10234", data["response"])

    def test_own_order_id_is_still_reported_by_a_second_tool(self):
        data = self.chat("CUS-001", "Where is my order ORD-10234?")
        self.assertIsNone(data["error_code"])
        self.assertIn("ORD-10234", data["response"])
        self.assertIn("FDX123456789", data["response"])

    def test_a_seeded_id_quoted_back_is_always_the_callers_own(self):
        for customer, order in (("CUS-001", "ORD-10234"), ("CUS-002", "ORD-10236")):
            with self.subTest(customer=customer):
                data = self.chat(customer, f"What is the status of my order {order}?")
                self.assertIn(order, data["response"])


class TestNoSeededIdentifierIsEmbeddedInSource(unittest.TestCase):
    """Static guard so the literals cannot be reintroduced anywhere else.

    ``init_db.py`` is the one legitimate home for seeded identifiers: it creates
    them. Every other module must be free of them.
    """

    def test_no_module_outside_the_seeder_embeds_a_seeded_identifier(self):
        from .helpers import BACKEND_ROOT

        app_dir = BACKEND_ROOT / "app"
        offenders = []
        for path in sorted(app_dir.rglob("*.py")):
            if path.name == "init_db.py":
                continue
            text = path.read_text(encoding="utf-8")
            hits = sorted(
                set(SEEDED_ORDERS & set(CONCRETE_ORDER.findall(text)))
                | set(SEEDED_CUSTOMERS & set(CONCRETE_CUSTOMER.findall(text)))
            )
            if hits:
                offenders.append(f"{path.name}: {hits}")
        self.assertEqual([], offenders, "seeded identifiers embedded in: " + "; ".join(offenders))

    def test_the_seeder_is_the_only_home_of_seeded_identifiers(self):
        """Guards the exemption above: init_db.py must still be the seeder."""
        from .helpers import BACKEND_ROOT

        seeder = (BACKEND_ROOT / "app" / "database" / "init_db.py").read_text(encoding="utf-8")
        self.assertEqual(
            SEEDED_ORDERS, set(CONCRETE_ORDER.findall(seeder)), "seeder order set changed"
        )


if __name__ == "__main__":
    unittest.main()