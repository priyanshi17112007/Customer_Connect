"""Escalation policy: the decision must follow the verified tool observations.

A damage / wrong-item / not-received claim used to escalate unconditionally, even
when a tool had already produced a complete, verified answer. These tests pin the
observation-driven behaviour and, just as importantly, pin that escalation still
happens whenever the verified data cannot close the claim.
"""

import unittest

from .helpers import auth_headers, get_client, reset_counters

# Seeded fixtures used below (see app/database/init_db.py seed_data):
#   ORD-10235  CUS-001  DELIVERED   delivered 15 days ago, no return -> eligible
#   ORD-10238  CUS-003  DELIVERED   delivered 15 days ago, no return -> eligible
#   ORD-10240  CUS-004  RETURNED    has return RET-3001 (processed)  -> ineligible
#   ORD-10234  CUS-001  OUT_FOR_DELIVERY, not delivered           -> ineligible
#   ORD-10236  CUS-002  SHIPPED     not delivered                  -> ineligible
RETURNABLE = "ORD-10235"
RETURNABLE_ALT = "ORD-10238"
RETURN_ALREADY_PROCESSED = "ORD-10240"
NOT_YET_DELIVERED = "ORD-10234"
IN_TRANSIT = "ORD-10236"


class EscalationTestCase(unittest.TestCase):
    def setUp(self):
        self.client = get_client()
        reset_counters()

    def chat(self, message, customer="CUS-001"):
        return self.client.post(
            "/api/v1/chat",
            json={"customer_id": customer, "message": message},
            headers=auth_headers(),
        )

    def data(self, message, customer="CUS-001"):
        res = self.chat(message, customer)
        self.assertEqual(200, res.status_code)
        return res.json()["data"]

    def tools_used(self, data):
        return {call["tool"] for call in data["tool_calls"]}


class TestVerifiedResolution(EscalationTestCase):
    """Verified observations that fully answer the claim must resolve it."""

    def test_wrong_item_with_eligible_return_resolves_without_a_ticket(self):
        data = self.data(f"I received the wrong product for order {RETURNABLE}.")
        self.assertIs(True, data["resolved"])
        self.assertIs(False, data["escalated"])
        self.assertIsNone(data["ticket_id"])
        self.assertEqual("complete", data["decision"])
        self.assertIsNone(data["error_code"])

    def test_damaged_item_with_eligible_return_resolves_without_a_ticket(self):
        data = self.data(f"My item arrived damaged, order {RETURNABLE_ALT}.",
                         customer="CUS-003")
        self.assertIs(True, data["resolved"])
        self.assertIs(False, data["escalated"])
        self.assertIsNone(data["ticket_id"])

    def test_resolution_only_states_verified_facts(self):
        data = self.data(f"I received the wrong product for order {RETURNABLE}.")
        response = data["response"]
        self.assertIn("eligible for return", response)
        # The agent has no refund/return tool, so it must not claim one ran.
        for forbidden in ("TKT-", "escalated", "refund has been issued",
                          "replacement", "we have processed", "has been refunded"):
            self.assertNotIn(forbidden.lower(), response.lower())

    def test_resolution_uses_the_return_tool_and_reports_no_ticket_step(self):
        data = self.data(f"I received the wrong product for order {RETURNABLE}.")
        self.assertIn("check_return_eligibility", self.tools_used(data))
        steps = [item["step"] for item in data["activity"]]
        self.assertNotIn("Support ticket created", steps)
        self.assertIn("Verified information gathered; resolution ready", steps)


class TestEscalationIsPreserved(EscalationTestCase):
    """Verified data that cannot close the claim must still reach a human."""

    def _assert_escalated(self, data):
        self.assertIs(True, data["escalated"])
        self.assertIs(False, data["resolved"])
        self.assertEqual("escalate", data["decision"])
        self.assertRegex(data["ticket_id"], r"^TKT-\d+$")

    def test_wrong_item_when_return_already_processed_escalates(self):
        data = self.data(f"I received the wrong item for order {RETURN_ALREADY_PROCESSED}.",
                         customer="CUS-004")
        self._assert_escalated(data)

    def test_damaged_item_when_parcel_not_delivered_escalates(self):
        data = self.data(f"My item is damaged and order {NOT_YET_DELIVERED} is still out for delivery.")
        self._assert_escalated(data)

    def test_not_received_claim_escalates_even_when_delivery_is_verified(self):
        data = self.data(f"I never received order {IN_TRANSIT}.", customer="CUS-002")
        self._assert_escalated(data)
        self.assertIn("get_delivery_status", self.tools_used(data))

    def test_escalated_reply_states_the_real_ticket_id(self):
        data = self.data(f"I received the wrong item for order {RETURN_ALREADY_PROCESSED}.",
                         customer="CUS-004")
        self.assertIn(data["ticket_id"], data["response"])

    def test_escalation_records_the_ticket_step(self):
        data = self.data(f"I received the wrong item for order {RETURN_ALREADY_PROCESSED}.",
                         customer="CUS-004")
        steps = [item["step"] for item in data["activity"]]
        self.assertIn("Support ticket created", steps)


class TestOwnershipIsUnaffected(EscalationTestCase):
    """The new resolution path must never bypass the ownership check."""

    def test_cross_customer_wrong_item_claim_is_denied(self):
        data = self.data(f"I received the wrong product for order {RETURNABLE_ALT}.")
        self.assertEqual("ORDER_NOT_ACCESSIBLE", data["error_code"])
        self.assertIs(False, data["resolved"])
        self.assertIs(False, data["escalated"])
        self.assertIsNone(data["ticket_id"])

    def test_cross_customer_claim_leaks_no_order_data(self):
        data = self.data(f"I received the wrong product for order {RETURNABLE_ALT}.")
        for secret in ("ORD-10238", "135.98", "UPS555666777", "Webcam HD"):
            self.assertNotIn(secret, data["response"])

    def test_unknown_order_damage_claim_never_resolves(self):
        data = self.data("I received the wrong product for order ORD-999999.")
        self.assertIs(False, data["resolved"])
        self.assertIsNone(data["ticket_id"])
        self.assertIsNotNone(data["error_code"])


if __name__ == "__main__":
    unittest.main()