from __future__ import annotations

import json
import sys
from typing import Any


def _extract_provider_names(litellm_module: Any) -> list[str]:
    candidates = [
        getattr(litellm_module, "LITELLM_CHAT_PROVIDERS", None),
        getattr(litellm_module, "provider_list", None),
        getattr(litellm_module, "litellm_provider_list", None),
    ]

    for candidate in candidates:
        if isinstance(candidate, (list, tuple, set)):
            names = [str(item).strip() for item in candidate if str(item).strip()]
            if names:
                return sorted(set(names))

    return []


def _default_base_url(provider_id: str) -> str:
    defaults = {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "azure": "https://{resource}.openai.azure.com/openai/deployments/{deployment}",
        "groq": "https://api.groq.com/openai/v1",
        "together_ai": "https://api.together.xyz/v1",
        "deepseek": "https://api.deepseek.com/v1",
    }
    return defaults.get(provider_id, "")


def _provider_label(provider_id: str) -> str:
    return provider_id.replace("_", " ").replace("-", " ").title()


def list_providers() -> list[dict[str, Any]]:
    import litellm  # type: ignore

    names = _extract_provider_names(litellm)
    if not names:
        raise RuntimeError("no providers discovered from litellm metadata")

    providers: list[dict[str, Any]] = []
    for provider_id in names:
        providers.append(
            {
                "id": provider_id,
                "label": _provider_label(provider_id),
                "defaultBaseUrl": _default_base_url(provider_id),
                "requiresModelId": True,
                "supportsReasoningTokens": provider_id in {"openai", "openrouter", "deepseek", "groq"},
            }
        )
    return providers


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
