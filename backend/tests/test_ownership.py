"""Ownership / IDOR protections.

These tests pin the authorization boundary the whole design rests on: order data
is released only to the customer the order belongs to. The end-to-end cases go
through the public HTTP API; the fail-closed cases exercise the public
``enforce_ownership`` method directly, because that branch is deliberately
unreachable through a successful API turn.

Seed data (app/database/init_db.py):
  CUS-001 John Smith   -> ORD-10234 (out for delivery, FDX123456789, 79.98)
                          ORD-10235 (delivered, UPS987654321, 79.99)
  CUS-002 Sarah Johnson-> ORD-10236 (shipped, DHL456789123, 149.99,
                          Mechanical Keyboard, 456 Oak Ave)
                          ORD-10237 (cancelled, refunded)
"""

import unittest

from .helpers import auth_headers, get_client, reset_counters

FOREIGN_ORDER = "ORD-10236"  # belongs to CUS-002
OWN_ORDER = "ORD-10234"  # belongs to CUS-001

# Everything the agent must never disclose about ORD-10236 to CUS-001.
FOREIGN_ORDER_SECRETS = (
    "DHL456789123",
    "149.99",
    "Mechanical Keyboard",
    "456 Oak Ave",
    "Sarah Johnson",
    "sarah.j@email.com",
)

# A second order-scoped tool the planner queues behind get_order. Reaching it
# requires the ownership gate to have already released the order, so it doubles
# as a second route to CUS-002's data.
SECOND_ORDER_TOOL_MESSAGES = {
    "payment": "What is the payment status for order ORD-10236?",
    "refund": "What is the refund status for order ORD-10236?",
    "return": "Can I return order ORD-10236?",
}


class OwnershipTestCase(unittest.TestCase):
    def setUp(self):
        self.client = get_client()
        reset_counters()

    def chat(self, message, customer, conversation_id=None):
        payload = {"customer_id": customer, "message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        res = self.client.post("/api/v1/chat", json=payload, headers=auth_headers())
        self.assertEqual(200, res.status_code)
        body = res.json()
        self.assertTrue(body["success"], body)
        return body["data"]

    def assertNoForeignData(self, data):
        """No fact *about* the foreign order may reach the wrong caller.

        The order id itself is excluded: the caller supplied it, so seeing it
        reflected in the activity trace discloses nothing.
        """
        blob = " ".join(
            [
                str(data.get("response") or ""),
                json_text(data.get("activity")),
                json_text(data.get("tool_calls")),
            ]
        )
        for secret in FOREIGN_ORDER_SECRETS:
            self.assertNotIn(secret, blob, f"leaked {secret!r}")
        # The user-facing reply must not confirm the id either.
        self.assertNotIn(FOREIGN_ORDER, str(data.get("response") or ""))


def json_text(value):
    import json

    return json.dumps(value, default=str, sort_keys=True)


class TestCrossCustomerOrderAccess(OwnershipTestCase):
    """Requirement 1: CUS-001 must not reach CUS-002's order."""

    def test_foreign_order_is_denied(self):
        data = self.chat(f"Where is my order {FOREIGN_ORDER}?", "CUS-001")
        self.assertEqual("ORDER_NOT_ACCESSIBLE", data["error_code"])
        self.assertIs(False, data["resolved"])

    def test_denial_discloses_no_protected_information(self):
        data = self.chat(f"Where is my order {FOREIGN_ORDER}?", "CUS-001")
        self.assertNoForeignData(data)

    def test_denial_does_not_resolve_or_open_a_ticket(self):
        data = self.chat(f"Where is my order {FOREIGN_ORDER}?", "CUS-001")
        self.assertIs(False, data["escalated"])
        self.assertIsNone(data["ticket_id"])

    def test_own_order_is_still_served(self):
        """Control: the denials above are about ownership, not a broken path."""
        data = self.chat(f"Where is my order {OWN_ORDER}?", "CUS-001")
        self.assertIsNone(data["error_code"])
        self.assertIn(OWN_ORDER, data["response"])
        self.assertIn("FDX123456789", data["response"])

    def test_owning_customer_can_still_reach_the_same_order(self):
        """Control: the denial is per-caller, not a global block on ORD-10236."""
        data = self.chat(f"Where is my order {FOREIGN_ORDER}?", "CUS-002")
        self.assertIsNone(data["error_code"])
        self.assertIn("DHL456789123", data["response"])

    def test_phrasing_that_defeats_intent_detection_still_leaks_nothing(self):
        """A message the intent detector does not recognise must stay fail-safe.

        "Is order X delivered?" currently classifies as ``general`` and queues
        no tools, so the ownership gate is never reached. The security-relevant
        invariant is only that no order data is released.
        """
        data = self.chat(f"Is order {FOREIGN_ORDER} delivered?", "CUS-001")
        self.assertNoForeignData(data)


class TestCrossCustomerToolAccess(OwnershipTestCase):
    """Requirement 2: a second order-scoped tool must not be a way around it."""

    def test_planner_really_does_queue_a_second_order_scoped_tool(self):
        """Guard the guard: confirm the test cases below are meaningful.

        If the planner stopped queueing a second tool, the "it never ran"
        assertions would pass vacuously.
        """
        from app.agents.support_agent import (
            ORDER_SCOPED_TOOLS,
            AgentIntent,
            ExtractedEntities,
            ToolSelector,
        )

        for intent in ("payment_status", "refund_status", "return_eligibility"):
            plan = ToolSelector.plan(
                AgentIntent(primary=intent),
                ExtractedEntities(order_id=FOREIGN_ORDER),
            )
            names = [step.name for step in plan]
            scoped = [name for name in names if name in ORDER_SCOPED_TOOLS]
            self.assertEqual(2, len(scoped), f"{intent} -> {names}")
            self.assertEqual("get_order", names[0])

    def test_second_tool_is_denied_access_to_a_foreign_order(self):
        for label, message in SECOND_ORDER_TOOL_MESSAGES.items():
            with self.subTest(tool=label):
                reset_counters()
                data = self.chat(message, "CUS-001")
                self.assertEqual("ORDER_NOT_ACCESSIBLE", data["error_code"])
                self.assertNoForeignData(data)

    def test_flow_stops_at_the_first_denied_tool(self):
        """The gate is fail-fast: the second tool is never executed at all."""
        for label, message in SECOND_ORDER_TOOL_MESSAGES.items():
            with self.subTest(tool=label):
                reset_counters()
                data = self.chat(message, "CUS-001")
                executed = [call["tool"] for call in data["tool_calls"]]
                self.assertEqual(["get_order"], executed)
                self.assertEqual("error", data["tool_calls"][0]["status"])

    def test_owning_customer_reaches_the_second_tool(self):
        data = self.chat(SECOND_ORDER_TOOL_MESSAGES["payment"], "CUS-002")
        self.assertIsNone(data["error_code"])
        executed = {call["tool"]: call["status"] for call in data["tool_calls"]}
        self.assertEqual("success", executed["get_order"])
        self.assertEqual("success", executed["get_payment_status"])


class TestConversationIsolation(OwnershipTestCase):
    """Requirement 3: cross-customer conversation access stays denied."""

    def test_replaying_another_customers_conversation_leaks_nothing(self):
        first = self.chat(f"Where is my order {OWN_ORDER}?", "CUS-001")
        conversation_id = first["conversation_id"]
        self.assertRegex(conversation_id, r"^CONV-\d{1,12}$")
        # Sanity: that first turn really did disclose CUS-001 order data.
        self.assertIn("FDX123456789", first["response"])

        replay = self.chat(
            f"Where is my order {OWN_ORDER}?", "CUS-002", conversation_id
        )
        self.assertNotIn("FDX123456789", replay["response"])
        self.assertNotIn(OWN_ORDER, replay["response"])
        self.assertNotEqual(conversation_id, replay["conversation_id"])

    def test_the_rejected_replay_leaks_nothing_either(self):
        first = self.chat(f"Where is my order {FOREIGN_ORDER}?", "CUS-002")
        self.assertIn("DHL456789123", first["response"])

        replay = self.chat(
            f"Where is my order {FOREIGN_ORDER}?", "CUS-001", first["conversation_id"]
        )
        self.assertNoForeignData(replay)

    def test_conversation_continues_for_its_owner(self):
        first = self.chat(f"Where is my order {OWN_ORDER}?", "CUS-001")
        second = self.chat(
            f"And what is the payment status for {OWN_ORDER}?",
            "CUS-001",
            first["conversation_id"],
        )
        self.assertEqual(first["conversation_id"], second["conversation_id"])
        self.assertIsNone(second["error_code"])

    def test_unknown_conversation_id_is_not_accepted(self):
        data = self.chat(
            f"Where is my order {OWN_ORDER}?",
            "CUS-001",
            conversation_id="CONV-999999999",
        )
        # The turn still runs, but on a fresh conversation.
        self.assertNotEqual("CONV-999999999", data["conversation_id"])


class TestFailClosedOwnership(unittest.TestCase):
    """Requirement 4: the fail-closed branches of ``enforce_ownership``.

    These branches are unreachable through a normal API turn (the loop aborts
    on the first denied tool), so they are covered directly against the public
    method.
    """

    def make_state(self, customer_id, verified=None):
        from app.agents.support_agent import (
            AgentExecutionState,
            AgentIntent,
            ExtractedEntities,
        )

        state = AgentExecutionState(
            intent=AgentIntent(primary="order_status"),
            entities=ExtractedEntities(order_id=OWN_ORDER),
            customer_id=customer_id,
        )
        if verified is not None:
            state.observations["get_order"] = verified
        return state

    def observation(self, tool, data):
        from app.agents.support_agent import ToolObservation

        return ToolObservation(
            tool=tool, params={"order_id": FOREIGN_ORDER}, ok=True, data=data
        )

    def enforce(self, observation, state):
        from app.agents.support_agent import SupportAgent

        return SupportAgent().enforce_ownership(observation, state)

    def assertDenied(self, observation, state):
        result = self.enforce(observation, state)
        self.assertIs(False, result.ok)
        self.assertEqual("ORDER_NOT_ACCESSIBLE", result.error_code)
        self.assertIsNone(result.data, "denied observation must not carry data")

    def test_absent_customer_identity_is_refused(self):
        self.assertDenied(
            self.observation(
                "get_order", {"order_id": OWN_ORDER, "customer_id": "CUS-001"}
            ),
            self.make_state(None),
        )

    def test_order_row_without_an_owner_is_refused(self):
        """The fail-closed core: a missing owner is never treated as a match."""
        self.assertDenied(
            self.observation("get_order", {"order_id": OWN_ORDER, "total": 79.98}),
            self.make_state("CUS-001"),
        )

    def test_second_tool_is_refused_without_a_verified_order(self):
        self.assertDenied(
            self.observation(
                "get_payment_status",
                {"order_id": OWN_ORDER, "status": "completed"},
            ),
            self.make_state("CUS-001"),
        )

    def test_second_tool_is_refused_when_verification_failed(self):
        from app.agents.support_agent import ToolObservation

        failed = ToolObservation(
            tool="get_order",
            params={"order_id": OWN_ORDER},
            ok=False,
            error_code="ORDER_NOT_ACCESSIBLE",
        )
        self.assertDenied(
            self.observation(
                "get_delivery_status",
                {"order_id": OWN_ORDER, "tracking_number": "FDX123456789"},
            ),
            self.make_state("CUS-001", verified=failed),
        )

    def test_second_tool_is_refused_for_a_verified_foreign_order(self):
        from app.agents.support_agent import ToolObservation

        verified = ToolObservation(
            tool="get_order",
            params={"order_id": FOREIGN_ORDER},
            ok=True,
            data={"order_id": FOREIGN_ORDER, "customer_id": "CUS-002"},
        )
        self.assertDenied(
            self.observation(
                "get_delivery_status",
                {"order_id": FOREIGN_ORDER, "tracking_number": "DHL456789123"},
            ),
            self.make_state("CUS-001", verified=verified),
        )

    def test_owner_match_is_allowed(self):
        from app.agents.support_agent import ToolObservation

        verified = ToolObservation(
            tool="get_order",
            params={"order_id": OWN_ORDER},
            ok=True,
            data={"order_id": OWN_ORDER, "customer_id": "CUS-001"},
        )
        state = self.make_state("CUS-001", verified=verified)
        order = self.observation(
            "get_order", {"order_id": OWN_ORDER, "customer_id": "CUS-001"}
        )
        self.assertIs(order, self.enforce(order, state))

        delivery = self.observation(
            "get_delivery_status",
            {"order_id": OWN_ORDER, "tracking_number": "FDX123456789"},
        )
        self.assertIs(delivery, self.enforce(delivery, state))

    def test_every_order_scoped_tool_is_gated(self):
        """No order-scoped tool may skip the check."""
        from app.agents.support_agent import ORDER_SCOPED_TOOLS

        self.assertIn("get_order", ORDER_SCOPED_TOOLS)
        for tool in sorted(ORDER_SCOPED_TOOLS):
            with self.subTest(tool=tool):
                self.assertDenied(
                    self.observation(tool, {"order_id": OWN_ORDER}), self.make_state(None)
                )


if __name__ == "__main__":
    unittest.main()
