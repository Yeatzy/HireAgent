import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dashscope

from app.config import Settings
from app.llm import QwenClient


def response(content: str):
    return SimpleNamespace(
        status_code=200,
        output=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content)
                )
            ]
        ),
    )


class LLMHarnessTests(unittest.TestCase):
    def test_invalid_json_is_retried_and_traced(self):
        client = QwenClient(Settings(api_key="test-key", ai_mode="auto"))
        client.begin_run()

        with patch.object(
            dashscope.Generation,
            "call",
            side_effect=[response("不是 JSON"), response('{"title": "测试岗"}')],
        ) as mocked:
            payload = client.call_json(
                "只输出 JSON",
                "<source_document>测试</source_document>",
                task="retry_test",
            )

        self.assertEqual(payload, {"title": "测试岗"})
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(client.call_traces[0].status, "success")
        self.assertEqual(client.call_traces[0].attempts, 2)
        self.assertTrue(client.enhanced_this_run)

    def test_schema_failure_can_mark_call_as_fallback(self):
        client = QwenClient(Settings(api_key="test-key", ai_mode="auto"))
        client.begin_run()

        with patch.object(
            dashscope.Generation,
            "call",
            return_value=response('{"unexpected": true}'),
        ):
            client.call_json("只输出 JSON", "data", task="schema_test")
        client.mark_invalid("schema_test", "缺少必要字段")

        self.assertEqual(client.call_traces[0].status, "fallback")
        self.assertFalse(client.enhanced_this_run)

    def test_failed_model_call_opens_circuit_for_run(self):
        client = QwenClient(
            Settings(
                api_key="test-key",
                ai_mode="auto",
                llm_max_attempts=1,
            )
        )
        client.begin_run()

        with patch.object(
            dashscope.Generation,
            "call",
            side_effect=RuntimeError("network timeout"),
        ) as mocked:
            first = client.call_json("只输出 JSON", "data", task="first")
            second = client.call_json("只输出 JSON", "data", task="second")

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(client.call_traces[0].status, "fallback")
        self.assertEqual(client.call_traces[1].status, "skipped")
        self.assertIn("熔断", client.call_traces[1].error)


if __name__ == "__main__":
    unittest.main()
