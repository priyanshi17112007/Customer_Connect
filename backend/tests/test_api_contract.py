"""Public API contract: health, authentication, chat, and error envelopes.

These tests drive the real FastAPI application over an in-process transport and
assert the contract the React frontend depends on.
"""

import unittest

from .helpers import auth_headers, get_client, reset_counters

ORDER_ID = "ORD-10234"


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = get_client()
        reset_counters()

    def chat(self, message, customer="CUS-001", key=None, conversation_id=None):
        payload = {"customer_id": customer, "message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return self.client.post("/api/v1/chat", json=payload, headers=auth_headers(key))


class TestHealth(ApiTestCase):
    def test_health_is_public_and_reports_state(self):
        res = self.client.get("/health")
        self.assertEqual(200, res.status_code)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual("ok", body["data"]["status"])
        self.assertTrue(body["data"]["version"])
        self.assertIn(body["data"]["database"], {"connected", "unavailable"})

    def test_health_needs_no_credentials(self):
        self.assertEqual(200, self.client.get("/health").status_code)

    def test_health_exposes_no_internals(self):
        raw = self.client.get("/health").text.lower()
        for leak in ("traceback", "sqlite", "select ", "/app/", "password"):
            self.assertNotIn(leak, raw)


class TestAuthentication(ApiTestCase):
    def test_missing_key_is_rejected(self):
        res = self.client.post("/api/v1/chat",
                               json={"customer_id": "CUS-001", "message": "hi"})
        self.assertEqual(401, res.status_code)

    def test_wrong_key_is_rejected(self):
        self.assertEqual(403, self.chat("hi", key="wrong-key").status_code)

    def test_auth_failure_message_is_generic(self):
        body = self.chat("hi", key="wrong-key").json()
        self.assertEqual("INVALID_API_KEY", body["error"]["code"])
        self.assertEqual("Authentication failed.", body["error"]["message"])

    def test_auth_failure_does_not_echo_either_key(self):
        raw = self.chat("hi", key="wrong-key").text
        self.assertNotIn("wrong-key", raw)

    def test_valid_key_is_accepted(self):
        self.assertEqual(200, self.chat("What is the status of my order ORD-10234?").status_code)


class TestChatContract(ApiTestCase):
    def test_successful_chat_returns_the_documented_fields(self):
        body = self.chat("What is the status of my order ORD-10234?").json()
        self.assertTrue(body["success"])
        data = body["data"]
        for field in ("conversation_id", "response", "intent", "decision",
                      "resolved", "escalated", "ticket_id", "error_code",
                      "activity", "tool_calls"):
            self.assertIn(field, data, f"ChatData.{field} is missing")

    def test_response_is_non_empty_text(self):
        data = self.chat("What is the status of my order ORD-10234?").json()["data"]
        self.assertIsInstance(data["response"], str)
        self.assertTrue(data["response"].strip())

    def test_conversation_id_matches_the_backend_pattern(self):
        data = self.chat("What is the status of my order ORD-10234?").json()["data"]
        self.assertRegex(data["conversation_id"], r"^CONV-\d{1,12}$")

    def test_activity_items_only_expose_safe_fields(self):
        data = self.chat("What is the status of my order ORD-10234?").json()["data"]
        self.assertIsInstance(data["activity"], list)
        self.assertTrue(data["activity"], "expected backend activity to be reported")
        for item in data["activity"]:
            self.assertLessEqual(set(item), {"step", "status", "details", "tool_name"})

    def test_tool_calls_never_include_arguments(self):
        data = self.chat("What is the status of my order ORD-10234?").json()["data"]
        for call in data["tool_calls"]:
            self.assertNotIn("arguments", call)
            self.assertNotIn("args", call)

    def test_invalid_customer_id_is_rejected(self):
        self.assertEqual(422, self.chat("hi", customer="not-a-customer").status_code)

    def test_unknown_order_reports_a_safe_error(self):
        data = self.chat("What is the status of my order ORD-999999?").json()["data"]
        self.assertEqual("INVALID_ORDER_ID", data["error_code"])

    def test_oversized_field_is_rejected(self):
        self.assertEqual(422, self.chat("x" * 2500).status_code)

    def test_extra_fields_are_rejected(self):
        res = self.client.post("/api/v1/chat",
                               json={"customer_id": "CUS-001", "message": "hi", "admin": True},
                               headers=auth_headers())
        self.assertEqual(422, res.status_code)


class TestErrorEnvelope(ApiTestCase):
    def _assert_envelope(self, body):
        self.assertIs(False, body["success"])
        self.assertEqual({"code", "message", "request_id"}, set(body["error"]))
        self.assertIsInstance(body["error"]["code"], str)
        self.assertIsInstance(body["error"]["request_id"], str)

    def test_validation_error_uses_the_envelope(self):
        self._assert_envelope(self.chat("hi", customer="nope").json())

    def test_auth_error_uses_the_envelope(self):
        self._assert_envelope(self.chat("hi", key="wrong-key").json())

    def test_errors_never_leak_internals(self):
        for res in (self.chat("hi", key="wrong-key"),
                    self.chat("hi", customer="nope"),
                    self.chat("What is the status of my order ORD-999999?")):
            raw = res.text.lower()
            for leak in ("traceback", "select ", "sqlite", "/app/", "line ", "exception"):
                self.assertNotIn(leak, raw)

    def test_oversized_body_returns_413_with_envelope(self):
        res = self.client.post("/api/v1/chat",
                               json={"customer_id": "CUS-001", "message": "x" * 20000},
                               headers=auth_headers())
        self.assertEqual(413, res.status_code)
        self._assert_envelope(res.json())


if __name__ == "__main__":
    unittest.main()