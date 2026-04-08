try:
    import litellm  # type: ignore
    import json
    models = litellm.models_by_provider.get("openai", [])
    print(json.dumps(list(models), ensure_ascii=False, indent=2))
except ImportError:
    models = []
