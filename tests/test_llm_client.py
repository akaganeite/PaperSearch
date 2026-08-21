import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from papersearch.llm_client import chat_json, extract_json


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class LlmClientTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "llm_summary": {
                "provider": "deepseek",
                "model": "deepseek-test",
                "base_url": "https://example.test",
                "api_key": "test-token-only",
                "timeout_seconds": 1,
            },
            "llm_prefilter": {"max_tokens": 100},
        }

    def test_extracts_json_from_wrapped_response(self):
        self.assertEqual(extract_json("result: {\"accept\": true}"), {"accept": True})

    def test_invalid_json_response_raises(self):
        response = FakeResponse({"choices": [{"message": {"content": "not json"}}]})
        with patch("papersearch.llm_client.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "invalid or empty JSON"):
                chat_json(self.config, "llm_prefilter", "system", "user")

    def test_http_auth_and_rate_limit_errors_are_reported(self):
        for status in (401, 429):
            error = urllib.error.HTTPError(
                "https://example.test/chat/completions",
                status,
                "error",
                {},
                io.BytesIO(b"request failed"),
            )
            with self.subTest(status=status), patch(
                "papersearch.llm_client.urllib.request.urlopen", side_effect=error
            ):
                with self.assertRaisesRegex(RuntimeError, f"LLM HTTP {status}"):
                    chat_json(self.config, "llm_prefilter", "system", "user")


if __name__ == "__main__":
    unittest.main()
