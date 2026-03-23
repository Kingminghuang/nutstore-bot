from __future__ import annotations

import json
import sys
from typing import Any

LITELLM_PROVIDERS = ("anthropic", "deepseek", "gemini")
OPENAI_PROVIDERS = ("openai",)
BUILTIN_PROVIDERS = LITELLM_PROVIDERS + OPENAI_PROVIDERS

try:
    from litellm.utils import supports_reasoning
except ImportError:

    def supports_reasoning(model: str, custom_llm_provider: str | None = None) -> bool:
        name = model.lower()
        return "reason" in name or "think" in name or "o1" in name or "o3" in name


def list_providers() -> list[dict[str, Any]]:

    def get_openai_reasoning_effort(model: str) -> list[str] | None:
        mapping = {
            "gpt-5.1": ["none", "low", "medium", "high"],
            "gpt-5": ["minimal", "low", "medium", "high"],
            "gpt-5-mini": ["minimal", "low", "medium", "high"],
            "gpt-5-nano": ["none", "low", "medium", "high"],
            "gpt-5-codex": ["low", "medium", "high"],
            "gpt-5.1-codex": ["low", "medium", "high"],
            "gpt-5.1-codex-mini": ["low", "medium", "high"],
            "gpt-5.1-codex-max": ["low", "medium", "high", "xhigh"],
            "gpt-5.2": ["none", "low", "medium", "high", "xhigh"],
            "gpt-5.2-pro": ["low", "medium", "high", "xhigh"],
            "gpt-5-pro": ["high"],
        }
        if model in mapping:
            return mapping[model]

        if "gpt-5" in model:
            if "-pro" in model:
                if "5.2" in model or "5.3" in model or "5.4" in model:
                    return ["low", "medium", "high", "xhigh"]
                return ["high"]
            if "-codex-max" in model:
                return ["low", "medium", "high", "xhigh"]
            if "-codex" in model:
                return ["low", "medium", "high"]
            if "5.2" in model or "5.3" in model or "5.4" in model:
                if "-mini" in model or "-nano" in model or "-chat" in model:
                    return ["none", "low", "medium", "high"]
                return ["none", "low", "medium", "high", "xhigh"]
            if "5.1" in model or "-nano" in model:
                return ["none", "low", "medium", "high"]
            return ["minimal", "low", "medium", "high"]
        return None

    def get_anthropic_reasoning_effort(model: str) -> list[str] | None:
        # Claude 3.7+ models support thinking
        if (
            "claude-3-7" in model
            or "claude-4" in model
            or "opus-4" in model
            or "sonnet-4" in model
        ):
            return ["low", "medium", "high"]
        return None

    def get_gemini_reasoning_effort(model: str) -> list[str] | None:
        if "gemini-3" in model:
            # Gemini 3+ use thinking_level which effectively maps to low/high
            return ["low", "high"]
        # Gemini 2.5 and earlier support full budget controls
        return ["none", "low", "medium", "high"]

    def get_deepseek_reasoning_effort(model: str) -> list[str] | None:
        lower = model.lower()
        if "reasoner" in lower or "reason" in lower:
            return ["none", "low", "medium", "high"]
        return None

    def build_models(
        models_list: list[str], fallback_provider: str
    ) -> list[dict[str, Any]]:
        result = []
        for m in models_list:
            model_info = {
                "id": m,
                "supportsReasoningTokens": supports_reasoning(
                    m, custom_llm_provider=fallback_provider
                ),
            }

            effort = None
            if fallback_provider == "openai":
                effort = get_openai_reasoning_effort(m)
            elif fallback_provider == "anthropic":
                effort = get_anthropic_reasoning_effort(m)
            elif fallback_provider == "gemini":
                effort = get_gemini_reasoning_effort(m)
            elif fallback_provider == "deepseek":
                effort = get_deepseek_reasoning_effort(m)

            if effort is not None:
                model_info["reasoningEffortValues"] = effort
                model_info["supportsReasoningTokens"] = True

            result.append(model_info)
        return result

    try:
        import litellm  # type: ignore

        models_map = getattr(litellm, "models_by_provider", {})
    except ImportError:
        models_map = {}

    return [
        {
            "id": "anthropic",
            "label": "Anthropic",
            "models": build_models(
                [
                    "claude-opus-4-6",
                    "claude-sonnet-4-6",
                    "claude-sonnet-4-5",
                    "claude-opus-4-5",
                ],
                fallback_provider="anthropic",
            ),
        },
        {
            "id": "deepseek",
            "label": "Deepseek",
            "models": build_models(
                [
                    "deepseek/deepseek-chat",
                    "deepseek/deepseek-reasoner",
                ],
                fallback_provider="deepseek",
            ),
        },
        {
            "id": "gemini",
            "label": "Gemini",
            "models": build_models(
                [
                    "gemini/gemini-2.5-flash-lite",
                    "gemini/gemini-2.5-flash",
                    "gemini/gemini-2.5-pro",
                    "gemini/gemini-3.1-pro-preview-customtools",
                    "gemini/gemini-3.1-pro-preview",
                    "gemini/gemini-3.1-flash-lite-preview",
                ],
                fallback_provider="gemini",
            ),
        },
        {
            "id": "openai",
            "label": "OpenAI / Compatible",
            "models": build_models(
                [
                    "gpt-5.2",
                    "gpt-5.2-pro",
                    "gpt-5.3-chat-latest",
                    "gpt-5.3-codex",
                    "gpt-5.4",
                    "gpt-5.4-mini",
                    "gpt-5.4-nano",
                    "gpt-5.4-pro",
                ],
                fallback_provider="openai",
            ),
        },
    ]


def main() -> int:
    try:
        payload = {"providers": list_providers()}
        print(json.dumps(payload, ensure_ascii=True), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
