from __future__ import annotations

import unittest

from python_runtime.direct_model import DirectModel, DirectModelConfig


class _FakeResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    def iter_lines(self, decode_unicode: bool = True):
        for line in self._lines:
            yield line

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DirectModelTests(unittest.TestCase):
    def test_generate_stream_handles_multiple_sse_chunks_in_one_line(self) -> None:
        model = DirectModel(
            DirectModelConfig(
                provider="openai",
                base_url="http://127.0.0.1:18000/v1",
                api_key="sk-test",
                model_id="gpt-4.1",
            )
        )

        fake = _FakeResponse(
            [
                'data: {"choices":[{"delta":{"content":"A"},"finish_reason":null}]}data: {"choices":[{"delta":{"content":"B"},"finish_reason":null}]}',
                "data: [DONE]",
            ]
        )

        model._post_completion = lambda body, stream: fake  # type: ignore[method-assign]

        deltas = list(
            model.generate_stream(
                messages=[{"role": "user", "content": "hello"}],
            )
        )

        texts = [delta.content for delta in deltas if delta.content]
        self.assertEqual(texts, ["A", "B"])


if __name__ == "__main__":
    unittest.main()
