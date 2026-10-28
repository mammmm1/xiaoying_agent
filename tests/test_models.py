import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from content_planner.agent import run_agent
from content_planner.models import DeepSeekModel, MockModel, ModelError
from content_planner.store import Store
from test_agent import request


class AdapterTests(unittest.TestCase):
    def payload(self, **changes):
        return {"id": "fixture-response", "model": "fixture-model", "usage": {"total_tokens": 12},
                "choices": [{"finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "reasoning_content": "INTERNAL_NOT_FOR_TRACE",
                    "tool_calls": [{"id": "c1", "type": "function", "function": {
                        "name": "search_assets", "arguments": '{"query":"字幕","reason":"检索素材"}'}}]}}], **changes}

    def respond(self, payload):
        return patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(json.dumps(payload).encode()))

    def test_native_tool_schema_and_api_key_not_logged(self):
        with self.respond(self.payload()) as mocked:
            model = DeepSeekModel(api_key="fake-test-key", base_url="https://api.deepseek.com", model="fixture-model")
            result, meta = model.decide([{"role": "user", "content": "测试"}], [])
            outgoing = mocked.call_args.args[0]
            body = json.loads(outgoing.data)
            self.assertEqual(outgoing.full_url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertNotIn("reasoning_content", result)
            self.assertNotIn("fake-test-key", json.dumps([result, meta]))
            self.assertEqual(result["tool_calls"][0]["function"]["name"], "search_assets")

    def test_http_error_redacts_server_body(self):
        error = urllib.error.HTTPError("https://api.deepseek.com", 401, "bad", {}, io.BytesIO(b"SECRET"))
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaisesRegex(ModelError, "401") as caught:
                DeepSeekModel(api_key="fake").decide([], [])
            self.assertNotIn("SECRET", str(caught.exception))

    def test_truncated_response_is_not_executed(self):
        payload = self.payload(choices=[{"finish_reason": "length", "message": {}}])
        with self.respond(payload):
            with self.assertRaisesRegex(ModelError, "未完成"):
                DeepSeekModel(api_key="fake").decide([], [])

    def test_malformed_response_fails_clearly(self):
        with self.respond({"choices": []}):
            with self.assertRaises(ModelError):
                DeepSeekModel(api_key="fake").decide([], [])

    def test_missing_key_and_insecure_url(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ModelError, "DEEPSEEK_API_KEY"):
                DeepSeekModel()
        with self.assertRaisesRegex(ModelError, "HTTPS"):
            DeepSeekModel(api_key="fake", base_url="http://example.com")


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_invalid_json_arguments_are_observed_and_can_be_corrected(self):
        model = MockModel()
        original = model.decide
        counter = 0

        def decide(messages, tools):
            nonlocal counter
            counter += 1
            if counter == 1:
                return {"role": "assistant", "content": "", "tool_calls": [{"id": "bad", "type": "function",
                    "function": {"name": "search_assets", "arguments": "not-json"}}]}, {}
            if counter == 2:
                return {"role": "assistant", "content": "", "tool_calls": [{"id": "corrected", "type": "function",
                    "function": {"name": "search_assets", "arguments": '{"query":"字幕","reason":"纠正参数"}'}}]}, {}
            return original(messages, tools)

        # Use a direct finish after proving recovery to avoid teaching the Mock malformed input.
        with patch.object(model, "decide", side_effect=decide):
            result = run_agent(request(), model, self.store, max_steps=2)
        self.assertEqual(result["trace"][0]["observation"]["error"]["code"], "INVALID_ARGUMENTS")
        self.assertTrue(result["trace"][1]["observation"]["ok"])

    def test_batch_calls_cause_no_partial_mutation(self):
        model = MockModel()
        calls = [{"id": str(i), "type": "function", "function": {
            "name": "search_assets", "arguments": '{"query":"字幕","reason":"批量"}'}} for i in range(2)]
        with patch.object(model, "decide", return_value=({"role": "assistant", "content": "", "tool_calls": calls}, {})):
            result = run_agent(request(), model, self.store, max_steps=1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(result["steps"], [])

    def test_plain_text_claim_does_not_mark_complete(self):
        model = MockModel()
        with patch.object(model, "decide", return_value=({"role": "assistant", "content": "已保存成功"}, {})):
            result = run_agent(request(), model, self.store, max_steps=2)
        self.assertFalse(result["saved"])
        self.assertEqual(result["error_code"], "STEP_LIMIT")


if __name__ == "__main__":
    unittest.main()
