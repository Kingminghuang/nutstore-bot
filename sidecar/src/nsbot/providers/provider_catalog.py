from __future__ import annotations

import json
import sys
from hashlib import sha256
from typing import Any

NUTSTORE_PROVIDER_ID = "nutstore"
NUTSTORE_BASE_URL = "https://ai-assistant.jianguoyun.net.cn/openid/openrouter"
NUTSTORE_MODELS = [
    "moonshotai/kimi-k2.6",
    "z-ai/glm-5.1",
    "qwen/qwen3.6-plus",
    "xiaomi/mimo-v2-pro",
    "minimax/minimax-m2.7",
    "openai/gpt-5.4",
    "google/gemini-3.1-pro-preview-customtools",
    "anthropic/claude-sonnet-4.6",
]
NUTSTORE_REASONING_EFFORTS = {
    "moonshotai/kimi-k2.6": ["enabled", "disabled"],
    "z-ai/glm-5.1": ["enabled", "disabled"],
    "qwen/qwen3.6-plus": ["enabled", "disabled"],
    "xiaomi/mimo-v2-pro": ["enabled", "disabled"],
}
NUTSTORE_UPSTREAM_PROVIDER_BY_PREFIX = {
    "openai/": "openai",
    "google/": "gemini",
    "anthropic/": "anthropic",
}

LITELLM_PROVIDERS = ("anthropic", "deepseek", "gemini")
HIDDEN_BASE_URL_PROVIDERS = LITELLM_PROVIDERS + (NUTSTORE_PROVIDER_ID,)
OPENAI_PROVIDERS = ("openai",)
BUILTIN_PROVIDERS = HIDDEN_BASE_URL_PROVIDERS + OPENAI_PROVIDERS


def base_url_policy(provider_id: str) -> str:
    if provider_id in HIDDEN_BASE_URL_PROVIDERS:
        return "hidden"
    if provider_id in OPENAI_PROVIDERS:
        return "optional"
    return "required"


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

    def get_provider_reasoning_effort(
        model: str, provider_id: str
    ) -> list[str] | None:
        if provider_id == "openai":
            return get_openai_reasoning_effort(model)
        if provider_id == "anthropic":
            return get_anthropic_reasoning_effort(model)
        if provider_id == "gemini":
            return get_gemini_reasoning_effort(model)
        if provider_id == "deepseek":
            return get_deepseek_reasoning_effort(model)
        return None

    def get_nutstore_reasoning_metadata(model: str) -> tuple[bool, list[str] | None]:
        effort = NUTSTORE_REASONING_EFFORTS.get(model)
        if effort is not None:
            return True, list(effort)

        if model == "minimax/minimax-m2.7":
            return True, None

        for prefix, upstream_provider in NUTSTORE_UPSTREAM_PROVIDER_BY_PREFIX.items():
            if not model.startswith(prefix):
                continue
            upstream_model = model[len(prefix) :]
            upstream_effort = get_provider_reasoning_effort(
                upstream_model, upstream_provider
            )
            supports_reasoning_tokens = (
                upstream_effort is not None
                or supports_reasoning(
                    upstream_model, custom_llm_provider=upstream_provider
                )
            )
            return supports_reasoning_tokens, upstream_effort

        return supports_reasoning(model, custom_llm_provider="openai"), None

    def get_reasoning_metadata(
        model: str, fallback_provider: str
    ) -> tuple[bool, list[str] | None]:
        if fallback_provider == NUTSTORE_PROVIDER_ID:
            return get_nutstore_reasoning_metadata(model)

        effort = get_provider_reasoning_effort(model, fallback_provider)
        supports_reasoning_tokens = (
            effort is not None
            or supports_reasoning(model, custom_llm_provider=fallback_provider)
        )
        return supports_reasoning_tokens, effort

    def build_models(
        models_list: list[str], fallback_provider: str
    ) -> list[dict[str, Any]]:
        result = []
        for m in models_list:
            supports_reasoning_tokens, effort = get_reasoning_metadata(
                m, fallback_provider
            )
            model_info = {
                "id": m,
                "supportsReasoningTokens": supports_reasoning_tokens,
            }

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
            "kind": "builtin",
            "runtimeProvider": "anthropic",
            "baseUrlPolicy": base_url_policy("anthropic"),
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
            "kind": "builtin",
            "runtimeProvider": "deepseek",
            "baseUrlPolicy": base_url_policy("deepseek"),
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
            "kind": "builtin",
            "runtimeProvider": "gemini",
            "baseUrlPolicy": base_url_policy("gemini"),
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
            "label": "OpenAI",
            "kind": "builtin",
            "runtimeProvider": "openai",
            "baseUrlPolicy": base_url_policy("openai"),
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
        {
            "id": NUTSTORE_PROVIDER_ID,
            "label": "Nutstore",
            "kind": "builtin",
            "runtimeProvider": "openai",
            "baseUrlPolicy": base_url_policy(NUTSTORE_PROVIDER_ID),
            "baseUrl": NUTSTORE_BASE_URL,
            "models": build_models(
                NUTSTORE_MODELS,
                fallback_provider=NUTSTORE_PROVIDER_ID,
            ),
        },
    ]


def catalog_version(providers: list[dict[str, Any]] | None = None) -> str:
    payload = providers if providers is not None else list_providers()
    normalized = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return sha256(normalized.encode("utf-8")).hexdigest()[:12]


def main() -> int:
    try:
        providers = list_providers()
        payload = {"version": catalog_version(providers), "providers": providers}
        print(json.dumps(payload, ensure_ascii=True), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
