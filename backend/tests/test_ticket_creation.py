"""Support ticket creation must survive ticket-id collisions.

Ticket ids keep the historical ``TKT-<digits>`` format inside a small numeric
space, so uniqueness is enforced by the database rather than by the generator.
These tests drive the real tool against the temporary test database and force
the collision that previously lost an escalation.
"""

import os
import unittest
from unittest import mock

from sqlalchemy.exc import IntegrityError

from .helpers import auth_headers, ensure_seeded, get_client, reset_counters

from app.tools import support_tools
from app.tools.support_tools import (
    TICKET_ID_ATTEMPTS,
    TICKET_ID_MAX,
    TICKET_ID_MIN,
    ToolError,
    create_support_ticket,
)


def draw_for(ticket_id):
    """The value ``secrets.randbelow`` must return to yield ``ticket_id``."""
    return int(ticket_id.split("-")[1]) - TICKET_ID_MIN


def number_of(ticket_id):
    return int(ticket_id.split("-")[1])


class TicketTestCase(unittest.TestCase):
    def setUp(self):
        ensure_seeded()
        reset_counters()

    def db_session(self):
        return support_tools.get_db_session()

    def existing_ticket_ids(self):
        db = self.db_session()
        try:
            return {t.ticket_id for t in db.query(support_tools.SupportTicket).all()}
        finally:
            db.close()

    def a_free_number(self):
        """A 5-digit number no ticket currently uses."""
        taken = self.existing_ticket_ids()
        candidate = 20000
        while f"TKT-{candidate}" in taken:
            candidate += 1
        self.assertLessEqual(candidate, TICKET_ID_MAX)
        return candidate


class TestNormalCreation(TicketTestCase):
    def test_ticket_is_created_with_the_documented_shape(self):
        result = create_support_ticket("CUS-001", "Escalation: parcel never arrived.")
        self.assertRegex(result["ticket_id"], r"^TKT-\d{5}$")
        self.assertEqual("open", result["status"])
        self.assertEqual("CUS-001", result["customer_id"])
        self.assertIn(result["ticket_id"], result["message"])
        self.assertTrue(result["created_at"])

    def test_generated_id_stays_inside_the_documented_range(self):
        for _ in range(25):
            result = create_support_ticket("CUS-001", "Range check.")
            self.assertGreaterEqual(number_of(result["ticket_id"]), TICKET_ID_MIN)
            self.assertLessEqual(number_of(result["ticket_id"]), TICKET_ID_MAX)

    def test_created_ticket_is_persisted_once(self):
        before = self.existing_ticket_ids()
        result = create_support_ticket("CUS-001", "Damaged item needs a human.")
        after = self.existing_ticket_ids()
        self.assertEqual(len(before) + 1, len(after))
        self.assertIn(result["ticket_id"], after)

    def test_unknown_customer_still_rejected(self):
        with self.assertRaises(ToolError) as ctx:
            create_support_ticket("CUS-999", "No such customer.")
        self.assertEqual("INVALID_CUSTOMER_ID", ctx.exception.error_code)


class TestCollisionRecovery(TicketTestCase):
    def test_forced_collision_is_retried_and_succeeds(self):
        taken = create_support_ticket("CUS-001", "First ticket, keeps the id.")["ticket_id"]
        free = self.a_free_number()

        # First draw collides; every later draw returns the free number.
        draws = [draw_for(taken)] + [draw_for(f"TKT-{free}")] * (TICKET_ID_ATTEMPTS + 2)
        with mock.patch.object(support_tools.secrets, "randbelow", side_effect=draws):
            result = create_support_ticket("CUS-001", "Second ticket, collides once.")

        self.assertEqual(f"TKT-{free}", result["ticket_id"])
        self.assertIn(result["ticket_id"], self.existing_ticket_ids())

    def test_repeated_collisions_still_recover(self):
        taken = create_support_ticket("CUS-001", "Anchor ticket.")["ticket_id"]
        free = self.a_free_number()
        # Three collisions in a row, then success.
        draws = ([draw_for(taken)] * 3
                 + [draw_for(f"TKT-{free}")] * (TICKET_ID_ATTEMPTS + 2))
        with mock.patch.object(support_tools.secrets, "randbelow", side_effect=draws):
            result = create_support_ticket("CUS-001", "Collides three times.")

        self.assertEqual(f"TKT-{free}", result["ticket_id"])

    def test_collision_never_overwrites_the_existing_ticket(self):
        taken = create_support_ticket("CUS-001", "Original subject line.")["ticket_id"]
        db = self.db_session()
        try:
            original = db.query(support_tools.SupportTicket).filter_by(ticket_id=taken).one()
            original_subject = original.subject
            original_id = original.id
        finally:
            db.close()

        free = self.a_free_number()
        draws = [draw_for(taken)] + [draw_for(f"TKT-{free}")] * 4
        with mock.patch.object(support_tools.secrets, "randbelow", side_effect=draws):
            create_support_ticket("CUS-001", "A completely different subject.")

        db = self.db_session()
        try:
            after = db.query(support_tools.SupportTicket).filter_by(ticket_id=taken).one()
            self.assertEqual(original_subject, after.subject)
            self.assertEqual(original_id, after.id)
        finally:
            db.close()

    def test_retry_exhaustion_returns_a_structured_database_error(self):
        taken = create_support_ticket("CUS-001", "Holds the only free id.")["ticket_id"]
        before = self.existing_ticket_ids()

        # Every draw collides, so the bounded budget is exhausted.
        with mock.patch.object(support_tools.secrets, "randbelow",
                               return_value=draw_for(taken)):
            with self.assertRaises(ToolError) as ctx:
                create_support_ticket("CUS-001", "Never finds a free id.")

        self.assertEqual("DATABASE_ERROR", ctx.exception.error_code)
        self.assertEqual("Failed to create support ticket", ctx.exception.message)
        # Nothing was written on the exhausted path.
        self.assertEqual(before, self.existing_ticket_ids())

    def test_retry_budget_is_finite(self):
        taken = create_support_ticket("CUS-001", "Anchor ticket.")["ticket_id"]
        calls = []

        def counting_randbelow(_n):
            calls.append(1)
            return draw_for(taken)

        with mock.patch.object(support_tools.secrets, "randbelow",
                               side_effect=counting_randbelow):
            with self.assertRaises(ToolError):
                create_support_ticket("CUS-001", "Exhausts the budget.")

        self.assertEqual(TICKET_ID_ATTEMPTS, len(calls))


class TestConflictDetection(unittest.TestCase):
    def test_ticket_id_unique_violation_is_recognised(self):
        collision = IntegrityError(
            "INSERT ...",
            {},
            Exception("UNIQUE constraint failed: support_tickets.ticket_id"),
        )
        self.assertTrue(support_tools._is_ticket_id_conflict(collision))

    def test_other_integrity_failures_are_not_retried(self):
        other = IntegrityError(
            "INSERT ...", {}, Exception("NOT NULL constraint failed: subject")
        )
        self.assertFalse(support_tools._is_ticket_id_conflict(other))

    def test_non_integrity_errors_are_not_treated_as_conflicts(self):
        self.assertFalse(support_tools._is_ticket_id_conflict(ValueError("nope")))


class TestEscalationStillWorks(unittest.TestCase):
    """The agent's escalation path must be unaffected by the retry logic."""

    def setUp(self):
        self.client = get_client()
        reset_counters()

    def test_escalation_returns_a_real_persisted_ticket(self):
        res = self.client.post(
            "/api/v1/chat",
            json={"customer_id": "CUS-002", "message": "I never received order ORD-10236."},
            headers=auth_headers(),
        )
        data = res.json()["data"]
        self.assertTrue(data["escalated"])
        self.assertRegex(data["ticket_id"], r"^TKT-\d{5}$")
        self.assertIn(data["ticket_id"], data["response"])

    def test_escalation_ticket_exists_in_the_database(self):
        res = self.client.post(
            "/api/v1/chat",
            json={"customer_id": "CUS-001",
                  "message": "My item is damaged, order ORD-10234."},
            headers=auth_headers(),
        )
        ticket_id = res.json()["data"]["ticket_id"]
        self.assertIsNotNone(ticket_id)
        db = support_tools.get_db_session()
        try:
            row = (db.query(support_tools.SupportTicket)
                     .filter_by(ticket_id=ticket_id).one())
            self.assertEqual("open", row.status.value)
            self.assertIsNotNone(row.customer_id)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()