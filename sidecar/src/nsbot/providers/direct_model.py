from __future__ import annotations

from dataclasses import dataclass

from smolagents.models import LiteLLMModel, Model, OpenAIModel

from nsbot.providers.provider_catalog import LITELLM_PROVIDERS, supports_reasoning


_THINKING_TYPE_MODELS = frozenset(
    {
        "moonshotai/kimi-k2.6",
        "z-ai/glm-5.1",
        "xiaomi/mimo-v2-pro",
    }
)
_ENABLE_THINKING_MODELS = frozenset({"qwen/qwen3.6-plus"})


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
    reasoning_effort: str | None = None


def _apply_openai_compatible_reasoning_selection(
    *, model_id: str, reasoning_effort: str | None, kwargs: dict[str, object]
) -> str | None:
    if reasoning_effort is None:
        return None

    normalized_model_id = model_id.strip().lower()
    normalized_effort = reasoning_effort.strip().lower()

    if normalized_model_id in _THINKING_TYPE_MODELS and normalized_effort in {
        "enabled",
        "disabled",
    }:
        kwargs["extra_body"] = {"thinking": {"type": normalized_effort}}
        return None

    if normalized_model_id in _ENABLE_THINKING_MODELS and normalized_effort in {
        "enabled",
        "disabled",
    }:
        kwargs["extra_body"] = {
            "enable_thinking": normalized_effort == "enabled"
        }
        return None

    return reasoning_effort


def DirectModel(config: DirectModelConfig) -> Model:
    provider = config.provider.strip().lower()

    if provider in LITELLM_PROVIDERS:
        model_id = config.model_id
        if not model_id.startswith(f"{provider}/"):
            model_id = f"{provider}/{model_id}"

        reasoning_effort = config.reasoning_effort
        if reasoning_effort is None and supports_reasoning(
            model_id, custom_llm_provider=provider
        ):
            reasoning_effort = "medium"

        kwargs: dict = {
            "model_id": model_id,
            "api_key": config.api_key,
        }
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        return LiteLLMModel(**kwargs)
    else:
        # Use OpenAIModel for openai and other OpenAI-compatible providers
        # Requires configuring base_url for non-default endpoints.
        kwargs: dict[str, object] = {
            "model_id": config.model_id,
        }

        if config.api_key and config.api_key.strip():
            kwargs["api_key"] = config.api_key.strip()

        base_url = config.base_url.strip() if config.base_url else ""
        if base_url:
            kwargs["api_base"] = base_url

        reasoning_effort = config.reasoning_effort
        reasoning_effort = _apply_openai_compatible_reasoning_selection(
            model_id=config.model_id,
            reasoning_effort=reasoning_effort,
            kwargs=kwargs,
        )
        if reasoning_effort is None and supports_reasoning(
            config.model_id, custom_llm_provider=provider
        ):
            reasoning_effort = "medium"

        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        return OpenAIModel(**kwargs)
