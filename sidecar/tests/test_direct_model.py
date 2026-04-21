from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast

from nsbot.providers.direct_model import DirectModel, DirectModelConfig


class DirectModelTests(unittest.TestCase):
    def test_generate_stream_uses_reasoning_effort_when_configured(self) -> None:
        model = DirectModel(
            DirectModelConfig(
                provider="openai",
                base_url="http://127.0.0.1:18000/v1",
                api_key="sk-test",
                model_id="gpt-4.1",
                reasoning_effort="high",
            )
        )

        captured: dict[str, Any] = {}

        def fake_retryer(fn, **kwargs):
            captured["kwargs"] = kwargs
            return iter(
                [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="A", tool_calls=None),
                                finish_reason=None,
                            )
                        ],
                    ),
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="B", tool_calls=None),
                                finish_reason=None,
                            )
                        ],
                    ),
                ]
            )

        model.retryer = fake_retryer  # type: ignore[assignment]

        direct_model = cast(Any, model)
        deltas = list(
            direct_model.generate_stream(
                messages=[{"role": "user", "content": "hello"}],
            )
        )

        texts = [delta.content for delta in deltas if delta.content]
        self.assertEqual(texts, ["A", "B"])
        self.assertEqual(captured["kwargs"]["reasoning_effort"], "high")

    def test_generate_stream_maps_kimi_thinking_selection_to_extra_body(self) -> None:
        model = DirectModel(
            DirectModelConfig(
                provider="openai",
                base_url="https://ai-assistant.jianguoyun.net.cn/openid/openrouter",
                api_key="sk-test",
                model_id="moonshotai/kimi-k2.6",
                reasoning_effort="enabled",
            )
        )

        captured: dict[str, Any] = {}

        def fake_retryer(fn, **kwargs):
            captured["kwargs"] = kwargs
            return iter(
                [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="A", tool_calls=None),
                                finish_reason=None,
                            )
                        ],
                    )
                ]
            )

        model.retryer = fake_retryer  # type: ignore[assignment]

        direct_model = cast(Any, model)
        list(direct_model.generate_stream(messages=[{"role": "user", "content": "hello"}]))

        self.assertEqual(
            captured["kwargs"]["extra_body"],
            {"thinking": {"type": "enabled"}},
        )
        self.assertNotIn("reasoning_effort", captured["kwargs"])

    def test_generate_stream_maps_qwen_thinking_selection_to_extra_body(self) -> None:
        model = DirectModel(
            DirectModelConfig(
                provider="openai",
                base_url="https://ai-assistant.jianguoyun.net.cn/openid/openrouter",
                api_key="sk-test",
                model_id="qwen/qwen3.6-plus",
                reasoning_effort="disabled",
            )
        )

        captured: dict[str, Any] = {}

        def fake_retryer(fn, **kwargs):
            captured["kwargs"] = kwargs
            return iter(
                [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="A", tool_calls=None),
                                finish_reason=None,
                            )
                        ],
                    )
                ]
            )

        model.retryer = fake_retryer  # type: ignore[assignment]

        direct_model = cast(Any, model)
        list(direct_model.generate_stream(messages=[{"role": "user", "content": "hello"}]))

        self.assertEqual(
            captured["kwargs"]["extra_body"],
            {"enable_thinking": False},
        )
        self.assertNotIn("reasoning_effort", captured["kwargs"])


if __name__ == "__main__":
    unittest.main()
