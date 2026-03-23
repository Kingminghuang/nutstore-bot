from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Generator

import requests

from smolagents.models import ChatMessage, ChatMessageStreamDelta, MessageRole, Model
from smolagents.monitoring import TokenUsage


class DirectModelError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class DirectModelConfig:
    provider: str
    base_url: str
    api_key: str
    model_id: str
    timeout_seconds: float = 60.0


@dataclass
class DirectToolCall:
    id: str
    name: str
    arguments: Any


@dataclass
class DirectChatResponse:
    content: str | None
    tool_calls: list[DirectToolCall]
    finish_reason: str | None
    raw: dict[str, Any]

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class DirectModel(Model):
    """Smolagents model adapter for direct OpenAI-compatible provider endpoints."""

    def __init__(self, config: DirectModelConfig):
        self.config = config
        super().__init__(model_id=config.model_id)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.config.api_key.strip() != "":
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _completion_url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _raise_provider_error(self, response: requests.Response) -> None:
        message = response.text
        code = "provider_unavailable"

        try:
            payload = response.json()
            if isinstance(payload, dict):
                if "error" in payload and isinstance(payload["error"], dict):
                    message = str(payload["error"].get("message") or message)
                else:
                    message = str(payload.get("message") or message)
        except Exception:
            pass

        if response.status_code in {401, 403}:
            code = "provider_auth_failed"
        elif response.status_code == 400:
            code = "provider_bad_request"
        elif response.status_code in {408, 429, 504}:
            code = "provider_timeout"
        elif response.status_code >= 500:
            code = "provider_unavailable"

        raise DirectModelError(code, message)

    def _post_completion(self, body: dict[str, Any], *, stream: bool) -> requests.Response:
        return requests.post(
            self._completion_url(),
            headers=self._headers(),
            json=body,
            timeout=self.config.timeout_seconds,
            stream=stream,
        )

    def _parse_tool_calls(self, message: dict[str, Any]) -> list[DirectToolCall]:
        raw_tool_calls = message.get("tool_calls") or []
        out: list[DirectToolCall] = []
        for index, item in enumerate(raw_tool_calls):
            if not isinstance(item, dict):
                continue
            function_data = item.get("function")
            if isinstance(function_data, dict):
                name = str(function_data.get("name") or "")
                arguments = function_data.get("arguments")
            else:
                name = str(item.get("name") or "")
                arguments = item.get("arguments")
            if name == "":
                continue
            out.append(
                DirectToolCall(
                    id=str(item.get("id") or f"tool-call-{index}"),
                    name=name,
                    arguments=arguments,
                )
            )
        return out

    def chat_with_retry(
        self,
        messages: list[ChatMessage | dict],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> DirectChatResponse:
        body: dict[str, Any] = {
            "model": model or self.config.model_id,
            "messages": messages,
            "stream": False,
        }
        if tools is not None:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

        response = self._post_completion(body, stream=False)
        if response.status_code >= 400:
            self._raise_provider_error(response)

        payload = response.json()
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return DirectChatResponse(
            content=message.get("content"),
            tool_calls=self._parse_tool_calls(message),
            finish_reason=choice.get("finish_reason"),
            raw=payload,
        )

    def generate(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from=None,
        **kwargs: Any,
    ) -> ChatMessage:
        body = self._prepare_completion_kwargs(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            model=self.config.model_id,
            **kwargs,
        )
        body["stream"] = False

        response = self._post_completion(body, stream=False)
        if response.status_code >= 400:
            self._raise_provider_error(response)

        payload = response.json()
        choice = payload["choices"][0]
        usage = payload.get("usage") or {}

        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=choice.get("message", {}).get("content", ""),
            raw=payload,
            token_usage=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
        )

    def generate_stream(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from=None,
        **kwargs: Any,
    ) -> Generator[ChatMessageStreamDelta]:
        body = self._prepare_completion_kwargs(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            model=self.config.model_id,
            **kwargs,
        )
        body["stream"] = True

        with self._post_completion(body, stream=True) as response:
            if response.status_code >= 400:
                self._raise_provider_error(response)

            done = False
            for raw_line in response.iter_lines(decode_unicode=True):
                if done:
                    break
                if raw_line is None:
                    continue
                line = raw_line.strip()
                if line == "" or "data:" not in line:
                    continue

                # Some provider implementations can coalesce multiple SSE data chunks
                # into one transport line. Split and process each logical chunk.
                payload_chunks = [chunk.strip() for chunk in line.split("data:") if chunk.strip() != ""]
                for payload_text in payload_chunks:
                    if payload_text == "[DONE]":
                        done = True
                        break

                    payload = json.loads(payload_text)
                    usage = payload.get("usage")
                    if usage:
                        yield ChatMessageStreamDelta(
                            content="",
                            token_usage=TokenUsage(
                                input_tokens=int(usage.get("prompt_tokens", 0)),
                                output_tokens=int(usage.get("completion_tokens", 0)),
                            ),
                        )

                    choices = payload.get("choices") or []
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    finish_reason = choice.get("finish_reason")
                    if finish_reason == "error":
                        raise DirectModelError("provider_unavailable", "provider stream returned finish_reason=error")

                    text = delta.get("content")
                    if text is not None:
                        yield ChatMessageStreamDelta(content=text)
