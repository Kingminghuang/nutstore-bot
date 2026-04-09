from __future__ import annotations

from dataclasses import dataclass

from smolagents.models import LiteLLMModel, Model, OpenAIModel

from nsbot_sidecar.providers.provider_catalog import LITELLM_PROVIDERS, supports_reasoning


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
        if reasoning_effort is None and supports_reasoning(
            config.model_id, custom_llm_provider=provider
        ):
            reasoning_effort = "medium"

        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        return OpenAIModel(**kwargs)
