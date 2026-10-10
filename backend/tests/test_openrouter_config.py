"""OpenRouter LLM configuration regression tests.

These tests verify that the OpenRouter provider migration works correctly:
- Default model and base URL are correct
- Missing API key does not crash
- API key is never included in logs/output
- LLMClient public interface remains unchanged
"""

import unittest
import os

# Ensure the test uses the example env values, not any real key
os.environ.pop("OPENROUTER_API_KEY", None)


class TestOpenRouterConfig(unittest.TestCase):
    def test_default_model_is_free_nemotron(self):
        from app.agents.llm_client import OPENROUTER_MODEL_DEFAULT

        self.assertEqual(OPENROUTER_MODEL_DEFAULT, "nvidia/nemotron-3-ultra-550b-a55b:free")

    def test_default_base_url_is_openrouter(self):
        from app.agents.llm_client import OPENROUTER_BASE_URL_DEFAULT

        self.assertEqual(OPENROUTER_BASE_URL_DEFAULT, "https://openrouter.ai/api/v1")

    def test_client_without_key_is_unavailable(self):
        from app.agents.llm_client import LLMClient

        client = LLMClient()
        self.assertFalse(client.available())
        self.assertEqual(client.configured_providers(), [])

    def test_client_with_openrouter_key_is_available(self):
        from app.agents.llm_client import LLMClient

        client = LLMClient(openrouter_api_key="sk-test-key-12345")
        self.assertTrue(client.available())
        self.assertIn("openrouter", client.configured_providers())

    def test_client_with_legacy_gemini_key_reports_gemini(self):
        """Backward compat: gemini_api_key -> provider 'gemini'."""
        from app.agents.llm_client import LLMClient

        client = LLMClient(gemini_api_key="AIzaTESTKEY1234567890")
        self.assertTrue(client.available())
        self.assertEqual(client.configured_providers(), ["gemini"])

    def test_client_with_legacy_groq_key_reports_groq(self):
        """Backward compat: groq_api_key -> provider 'groq'."""
        from app.agents.llm_client import LLMClient

        client = LLMClient(groq_api_key="gsk_testkey1234567890")
        self.assertTrue(client.available())
        self.assertEqual(client.configured_providers(), ["groq"])

    def test_explicit_openrouter_key_reports_openrouter(self):
        from app.agents.llm_client import LLMClient

        client = LLMClient(openrouter_api_key="sk-test")
        self.assertEqual(client.configured_providers(), ["openrouter"])

    def test_api_key_not_in_configured_providers_output(self):
        """Secrets never appear in provider listing."""
        from app.agents.llm_client import LLMClient

        secret = "sk-REAL_SECRET_KEY_12345"
        client = LLMClient(openrouter_api_key=secret)
        providers = client.configured_providers()
        self.assertNotIn(secret, str(providers))

    def test_api_key_not_in_error_messages(self):
        """Secrets are scrubbed from error messages."""
        from app.agents.llm_client import LLMClient

        secret = "sk-REAL_SECRET_KEY_12345"
        client = LLMClient(openrouter_api_key=secret, chat_models={
            "openrouter": type("FailModel", (), {
                "invoke": lambda self, msg: (_ for _ in ()).throw(RuntimeError(f"auth failed for {secret}"))
            })()
        })
        result = client.classify_intent("hello")
        self.assertFalse(result.ok)
        self.assertNotIn(secret, str(result.error_message))
        self.assertIn("[redacted]", str(result.error_message))

    def test_llmresult_interface_unchanged(self):
        """LLMResult dataclass fields unchanged."""
        from app.agents.llm_client import LLMResult

        r = LLMResult(ok=True, value="test", provider="openrouter")
        self.assertTrue(r.ok)
        self.assertEqual(r.value, "test")
        self.assertEqual(r.provider, "openrouter")
        self.assertIsNone(r.error_code)
        self.assertIsNone(r.error_message)
        self.assertFalse(r.failed)

        r2 = LLMResult(ok=False, error_code="TEST_ERROR", error_message="test msg", provider="openrouter")
        self.assertFalse(r2.ok)
        self.assertEqual(r2.error_code, "TEST_ERROR")
        self.assertEqual(r2.error_message, "test msg")
        self.assertEqual(r2.provider, "openrouter")
        self.assertTrue(r2.failed)

    def test_llmclient_public_methods_exist(self):
        """LLMClient public interface unchanged."""
        from app.agents.llm_client import LLMClient

        client = LLMClient()
        self.assertTrue(callable(getattr(client, "classify_intent", None)))
        self.assertTrue(callable(getattr(client, "extract_entities", None)))
        self.assertTrue(callable(getattr(client, "generate_response", None)))
        self.assertTrue(callable(getattr(client, "available", None)))
        self.assertTrue(callable(getattr(client, "configured_providers", None)))

    def test_classify_intent_returns_llmresult(self):
        from app.agents.llm_client import LLMClient, LLMResult

        client = LLMClient()
        result = client.classify_intent("where is my order")
        self.assertIsInstance(result, LLMResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "NO_PROVIDER")

    def test_extract_entities_returns_llmresult(self):
        from app.agents.llm_client import LLMClient, LLMResult

        client = LLMClient()
        result = client.extract_entities("my order ORD-10234")
        self.assertIsInstance(result, LLMResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "NO_PROVIDER")

    def test_generate_response_returns_llmresult(self):
        from app.agents.llm_client import LLMClient, LLMResult

        client = LLMClient()
        result = client.generate_response("hi", {"fact": "test"}, "base reply")
        self.assertIsInstance(result, LLMResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "NO_PROVIDER")

    def test_scrub_secrets_removes_openrouter_keys(self):
        from app.agents.llm_client import scrub_secrets

        secret = "sk-REAL_SECRET_KEY_12345"
        text = f"Error: auth failed for key {secret}"
        cleaned = scrub_secrets(text, [secret])
        self.assertNotIn(secret, cleaned)
        self.assertIn("[redacted]", cleaned)

    def test_scrub_secrets_removes_generic_patterns(self):
        from app.agents.llm_client import scrub_secrets

        text = "api_key=sk-abc123 and password=secret"
        cleaned = scrub_secrets(text)
        self.assertNotIn("sk-abc123", cleaned)
        self.assertNotIn("secret", cleaned)
        self.assertIn("[redacted]", cleaned)


if __name__ == "__main__":
    unittest.main()