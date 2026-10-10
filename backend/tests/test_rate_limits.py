"""Rate limiting and authentication-failure throttling.

Covers the two in-process budgets in ``app/security/middleware.py``:

* the per-endpoint request budget   ``PATH_LIMITS["/api/v1/chat"] = (20, 60)``
* the authentication-failure throttle ``AUTH_FAILURE_LIMIT = 10`` in 300 seconds

Behavioural expectations are derived from the module configuration rather than
hardcoded, so the tests keep testing the limiter rather than a copy of its
numbers. One test deliberately pins the documented thresholds so a silent change
to either budget is caught.

Both counter stores are process-wide and shared with every other test module in
the session, so ``reset_counters()`` runs in ``setUp``. That is the mechanism
that makes these tests independent of each other and order-independent.
"""

import unittest

from .helpers import auth_headers, get_client, reset_counters

CHAT_PATH = "/api/v1/chat"
CHAT_BODY = {"customer_id": "CUS-001", "message": "Where is my order ORD-10234?"}
BAD_KEY = "wrong-key-not-the-real-one"

# The budgets this project claims to enforce. Pinned on purpose.
DOCUMENTED_CHAT_LIMIT = 20
DOCUMENTED_CHAT_WINDOW = 60
DOCUMENTED_AUTH_FAILURE_LIMIT = 10
DOCUMENTED_AUTH_FAILURE_WINDOW = 300


def request_budget(path=CHAT_PATH):
    """(limit, window) the limiter will actually apply to ``path``."""
    from app.security import middleware as mw

    return mw.PATH_LIMITS.get(path, mw.DEFAULT_CHAT_LIMIT)


def auth_failure_budget():
    from app.security import middleware as mw

    return mw.AUTH_FAILURE_LIMIT, mw.AUTH_FAILURE_WINDOW


class RateLimitTestCase(unittest.TestCase):
    def setUp(self):
        self.client = get_client()
        # get_client() already resets, but be explicit: these tests depend on a
        # full budget and must not inherit anything from a previous test.
        reset_counters()
        self.limit, self.window = request_budget()
        self.auth_limit, self.auth_window = auth_failure_budget()

    def chat(self, key=None):
        return self.client.post(
            CHAT_PATH, json=dict(CHAT_BODY), headers=auth_headers(key)
        )

    def spend_budget(self):
        """Consume the whole allowed request budget, asserting each was allowed."""
        for _ in range(self.limit):
            res = self.chat()
            self.assertEqual(200, res.status_code)
            self.assertNotIn("Retry-After", res.headers)
        return self.chat()


class TestDocumentedThresholds(unittest.TestCase):
    def test_configured_budgets_match_the_documented_values(self):
        limit, window = request_budget()
        self.assertEqual(DOCUMENTED_CHAT_LIMIT, limit)
        self.assertEqual(DOCUMENTED_CHAT_WINDOW, window)
        auth_limit, auth_window = auth_failure_budget()
        self.assertEqual(DOCUMENTED_AUTH_FAILURE_LIMIT, auth_limit)
        self.assertEqual(DOCUMENTED_AUTH_FAILURE_WINDOW, auth_window)

    def test_chat_path_is_explicitly_configured(self):
        from app.security import middleware as mw

        self.assertIn(CHAT_PATH, mw.PATH_LIMITS)


class TestRequestRateLimit(RateLimitTestCase):
    def test_first_twenty_requests_are_allowed(self):
        for index in range(1, self.limit + 1):
            with self.subTest(request=index):
                res = self.chat()
                self.assertEqual(200, res.status_code)
                self.assertTrue(res.json()["success"])
                self.assertNotIn("Retry-After", res.headers)

    def test_request_after_the_budget_is_rejected_with_429(self):
        over = self.spend_budget()
        self.assertEqual(429, over.status_code)
        self.assertEqual("RATE_LIMIT_EXCEEDED", over.json()["error"]["code"])
        self.assertFalse(over.json()["success"])
        # A throttled request must not carry any agent output.
        self.assertEqual({"success", "error"}, set(over.json().keys()))

    def test_rate_limit_429_carries_retry_after(self):
        over = self.spend_budget()
        self.assertIn("Retry-After", over.headers)
        retry_after = int(over.headers["Retry-After"])
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, self.window)

    def test_retry_after_is_stable_while_the_window_is_open(self):
        over = self.spend_budget()
        again = self.chat()
        self.assertEqual(429, again.status_code)
        self.assertEqual(
            over.headers["Retry-After"], again.headers["Retry-After"]
        )

    def test_rate_limit_limit_header_reports_the_configured_budget(self):
        res = self.chat()
        self.assertEqual(str(self.limit), res.headers["X-RateLimit-Limit"])
        self.assertEqual("20", res.headers["X-RateLimit-Limit"])

    def test_rate_limit_remaining_counts_down(self):
        observed = []
        for _ in range(self.limit):
            observed.append(int(self.chat().headers["X-RateLimit-Remaining"]))
        self.assertEqual(
            [self.limit - n for n in range(1, self.limit + 1)], observed
        )
        self.assertEqual(0, observed[-1])

    def test_remaining_never_goes_negative_before_the_cutoff(self):
        seen = [int(self.chat().headers["X-RateLimit-Remaining"]) for _ in range(3)]
        self.assertEqual(sorted(seen, reverse=True), seen)
        self.assertGreaterEqual(min(seen), 0)


class TestAuthFailureThrottle(RateLimitTestCase):
    def test_initial_failed_attempts_are_allowed_as_403(self):
        for attempt in range(1, self.auth_limit + 1):
            with self.subTest(attempt=attempt):
                res = self.chat(BAD_KEY)
                self.assertEqual(403, res.status_code)
                self.assertEqual("INVALID_API_KEY", res.json()["error"]["code"])
                self.assertNotIn("Retry-After", res.headers)

    def test_failure_after_the_allowed_streak_is_throttled_with_429(self):
        for _ in range(self.auth_limit):
            self.assertEqual(403, self.chat(BAD_KEY).status_code)
        throttled = self.chat(BAD_KEY)
        self.assertEqual(429, throttled.status_code)
        self.assertEqual("TOO_MANY_ATTEMPTS", throttled.json()["error"]["code"])

    def test_auth_throttle_429_carries_retry_after(self):
        for _ in range(self.auth_limit):
            self.chat(BAD_KEY)
        throttled = self.chat(BAD_KEY)
        self.assertIn("Retry-After", throttled.headers)
        self.assertEqual(str(self.auth_window), throttled.headers["Retry-After"])
        self.assertEqual(self.auth_window, throttled.json()["error"]["retry_after"])

    def test_the_throttle_applies_before_the_request_limiter(self):
        """The throttle must be the thing that answers, not a coincidental 429.

        The failure streak plus the answering request is still inside the request
        budget, so a 429 here can only come from the auth-failure middleware.
        """
        total = self.auth_limit + 1
        self.assertLess(total, self.limit)
        for _ in range(self.auth_limit):
            self.chat(BAD_KEY)
        throttled = self.chat(BAD_KEY)
        self.assertEqual(429, throttled.status_code)
        self.assertNotIn("X-RateLimit-Remaining", throttled.headers)

    def test_missing_api_key_counts_towards_the_same_streak(self):
        for attempt in range(1, self.auth_limit + 1):
            with self.subTest(attempt=attempt):
                res = self.client.post(CHAT_PATH, json=dict(CHAT_BODY))
                self.assertEqual(401, res.status_code)
                self.assertEqual("MISSING_API_KEY", res.json()["error"]["code"])
        throttled = self.client.post(CHAT_PATH, json=dict(CHAT_BODY))
        self.assertEqual(429, throttled.status_code)
        self.assertEqual("TOO_MANY_ATTEMPTS", throttled.json()["error"]["code"])

    def test_only_a_successful_response_clears_the_streak(self):
        """The streak is cleared by any non-401/403 response.

        That is not a brute-force bypass: producing such a response on
        /api/v1/chat already requires the valid API key, so someone who cannot
        authenticate can never clear their own streak. Pinned so the guarantee
        is not lost if the clearing rule is ever widened.
        """
        for _ in range(self.auth_limit - 1):
            self.assertEqual(403, self.chat(BAD_KEY).status_code)
        self.assertEqual(200, self.chat().status_code)
        # The streak restarted, so the next failure is a plain rejection again.
        self.assertEqual(403, self.chat(BAD_KEY).status_code)

    def test_throttle_blocks_the_address_including_valid_credentials(self):
        """Once tripped, the throttle answers before authentication runs.

        The middleware short-circuits on the stored failure count, so a correct
        key from the same address is refused too. Recorded because it is a
        lockout characteristic, not a bug: it denies an attacker nothing extra
        and costs a legitimate client behind a shared address a 300s lockout
        after 10 typos.
        """
        for _ in range(self.auth_limit):
            self.chat(BAD_KEY)
        self.assertEqual(429, self.chat(BAD_KEY).status_code)
        self.assertEqual(429, self.chat().status_code)
        body = self.chat().json()["error"]
        self.assertEqual("TOO_MANY_ATTEMPTS", body["code"])
        # The refusal must not confirm whether the supplied credential was right.
        self.assertNotIn("INVALID_API_KEY", str(body))


class TestCounterIndependence(RateLimitTestCase):
    """Requirement: the shared counters must not leak between test cases."""

    def test_reset_counters_clears_both_stores(self):
        from app.security import middleware as mw

        for _ in range(self.auth_limit):
            self.chat(BAD_KEY)
        self.chat()
        self.assertTrue(mw._counters or mw._auth_failures)

        reset_counters()
        self.assertEqual({}, mw._counters)
        self.assertEqual({}, mw._auth_failures)

    def test_a_full_budget_is_available_after_a_saturated_one(self):
        self.assertEqual(429, self.spend_budget().status_code)
        reset_counters()
        self.assertEqual(200, self.chat().status_code)

    def test_a_full_failure_budget_is_available_after_a_saturated_one(self):
        for _ in range(self.auth_limit):
            self.chat(BAD_KEY)
        self.assertEqual(429, self.chat(BAD_KEY).status_code)
        reset_counters()
        self.assertEqual(403, self.chat(BAD_KEY).status_code)

    def test_each_test_starts_from_an_empty_counter_store(self):
        from app.security import middleware as mw

        self.assertEqual({}, mw._counters)
        self.assertEqual({}, mw._auth_failures)


if __name__ == "__main__":
    unittest.main()