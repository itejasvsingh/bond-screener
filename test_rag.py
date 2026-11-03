import json
import unittest
from datetime import date
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import rag
import server
from api import chat as vercel_chat


class RagTests(unittest.TestCase):
    def setUp(self):
        self.bonds = [
            {
                "isin": "INAAA0000001", "sym": "PFC2030", "name": "PFC", "issuer": "Power Finance Corporation",
                "issueDesc": "Power Finance 7.5% 2030", "kind": "CORP", "cat": "PSU", "rating": "AAA",
                "agency": "CRISIL", "cpn": 7.5, "freq": 2, "fObs": 2, "price": 98, "face": 100,
                "mat": "2030-12-31", "liq": {"days": 20, "sessions": 30},
            },
            {
                "isin": "INBBB0000002", "sym": "TATA2031", "name": "TATA", "issuer": "Tata Capital",
                "kind": "CORP", "cat": "Corporate", "rating": "AA", "cpn": 8, "freq": 1,
                "price": 100, "face": 100, "mat": "2031-12-31",
            },
        ]

    def test_retrieves_bond_and_issuer_sources(self):
        sources = rag.retrieve("Power Finance Corporation rating", self.bonds, today=date(2026, 1, 1))
        self.assertEqual(sources[0]["id"], "INAAA0000001")
        self.assertIn("CRISIL", sources[0]["text"])

    def test_retrieves_ticker_prefix_and_coupon_decimal(self):
        sources = rag.retrieve("PFC 7.5% coupon", self.bonds, today=date(2026, 1, 1))
        self.assertEqual(sources[0]["id"], "INAAA0000001")

    def test_answers_from_retrieved_data_without_model(self):
        with patch.dict("os.environ", {}, clear=True):
            result = rag.answer("PFC2030 rating and coupon", self.bonds)
        self.assertEqual(result["mode"], "retrieval")
        self.assertIn("AAA", result["answer"])
        self.assertTrue(result["sources"])

    def test_highest_yield_query_ranks_available_ytm_first(self):
        result = rag.retrieve("highest available yield to maturity", self.bonds, today=date(2026, 1, 1), limit=2)
        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(result[0]["ytm"], result[1]["ytm"])

    def test_empty_and_oversized_questions_are_rejected(self):
        with self.assertRaises(rag.ChatError):
            rag.answer("   ", self.bonds)
        with self.assertRaises(rag.ChatError):
            rag.answer("x" * (rag.MAX_QUESTION_LENGTH + 1), self.bonds)

    def test_partial_provider_configuration_is_reported(self):
        with patch.dict("os.environ", {"RAG_MODEL": "test-model"}, clear=True):
            with self.assertRaisesRegex(rag.ChatError, "RAG_API_URL"):
                rag.answer("PFC2030?", self.bonds)

    def test_openai_key_alone_uses_vercel_default_model(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            config = rag._model_configuration()
        self.assertEqual(config, (
            "https://api.openai.com/v1/chat/completions",
            "gpt-4o-mini",
            "test-key",
        ))

    def test_invalid_provider_url_is_reported(self):
        config = {"RAG_API_URL": "https://", "RAG_MODEL": "test-model"}
        with patch.dict("os.environ", config, clear=True):
            with self.assertRaisesRegex(rag.ChatError, "valid http"):
                rag.answer("PFC2030?", self.bonds)

    def test_provider_401_explains_expected_key_without_exposing_it(self):
        error = HTTPError("https://api.openai.com", 401, "Unauthorized", {}, None)
        with patch.object(rag, "urlopen", side_effect=error):
            with self.assertRaises(rag.ProviderError) as raised:
                rag._generate("question", [], (
                    "https://api.openai.com/v1/chat/completions",
                    "gpt-4o-mini",
                    "secret-test-key",
                ))
        self.assertIn("active OpenAI key", str(raised.exception))
        self.assertIn("API_KEY is not used", str(raised.exception))
        self.assertNotIn("secret-test-key", str(raised.exception))

    def test_configured_provider_generates_from_retrieved_sources(self):
        config = {
            "RAG_API_URL": "http://localhost:11434/v1/chat/completions",
            "RAG_MODEL": "test-model",
        }
        with patch.dict("os.environ", config, clear=True), \
                patch.object(rag, "_generate", return_value="Grounded answer [INAAA0000001]") as generate:
            result = rag.answer("PFC2030 rating and coupon", self.bonds)
        self.assertEqual(result["mode"], "generated")
        self.assertEqual(result["answer"], "Grounded answer [INAAA0000001]")
        generate.assert_called_once()

    def test_chat_api_returns_grounded_answer_and_sources(self):
        httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
        thread = Thread(target=httpd.serve_forever)
        thread.start()
        try:
            with patch.object(server, "current", return_value={"bonds": self.bonds}), \
                    patch.dict("os.environ", {}, clear=True):
                request = Request(
                    "http://127.0.0.1:%d/api/chat" % httpd.server_port,
                    data=b'{"question":"PFC2030 rating and coupon"}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
            self.assertEqual(result["mode"], "retrieval")
            self.assertTrue(result["sources"])
            self.assertIn("AAA", result["answer"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_vercel_function_reads_data_and_returns_sources(self):
        with patch.object(vercel_chat, "load_data", return_value=(self.bonds, {})), \
                patch.dict("os.environ", {}, clear=True):
            result = vercel_chat.run_chat("PFC2030 rating and coupon")
        self.assertEqual(result["mode"], "retrieval")
        self.assertEqual(result["sources"][0]["id"], "INAAA0000001")

    def test_vercel_http_function_does_not_expose_api_key(self):
        httpd = HTTPServer(("127.0.0.1", 0), vercel_chat.handler)
        thread = Thread(target=httpd.serve_forever)
        thread.start()
        try:
            with patch.object(vercel_chat, "load_data", return_value=(self.bonds, {})), \
                    patch.object(rag, "_generate", return_value="Grounded answer [INAAA0000001]"), \
                    patch.dict("os.environ", {"OPENAI_API_KEY": "server-only-test-key"}, clear=True):
                request = Request(
                    "http://127.0.0.1:%d/api/chat" % httpd.server_port,
                    data=b'{"question":"PFC2030 rating and coupon"}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    body = response.read().decode("utf-8")
            self.assertIn("Grounded answer", body)
            self.assertNotIn("server-only-test-key", body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
