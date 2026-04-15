#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NS_BOT_HOME="$(mktemp -d "${TMPDIR:-/tmp}/sidecar-cli-demo.XXXXXX")"
trap 'rm -rf "$NS_BOT_HOME"' EXIT

if command -v uv >/dev/null 2>&1; then
  PY_RUN=(uv run python)
else
  PY_RUN=(python3)
fi

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "[*] Using NS_BOT_HOME: $NS_BOT_HOME"

SEED_JSON="$(${PY_RUN[@]} - "$NS_BOT_HOME" <<'PY'
import json
import sys

from nsbot_sidecar.providers.provider_catalog import list_providers
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore, ProviderSecretPayload
from nsbot_sidecar.infrastructure.storage import connect_database

ns_bot_home = sys.argv[1]
db = connect_database(ns_bot_home)
repos = create_repositories(db)
store = LocalSecretStore(ns_bot_home)

openai_models = []
for provider in list_providers():
    if str(provider.get("id") or "") == "openai":
        openai_models = [str(item.get("id") or "") for item in provider.get("models", [])]
        break

if len(openai_models) < 2:
    raise RuntimeError("Expected at least 2 OpenAI catalog models")

repos.providers.save_bundle(
    connection_data={
        "id": "prov_openai_demo",
        "kind": "builtin",
        "runtime_provider": "openai",
        "catalog_provider_id": "openai",
        "display_name": "OpenAI Demo",
        "base_url": None,
        "secret_ref": "sec_prov_openai_demo",
        "api_key_configured": True,
        "model_policy": "restricted",
        "preferred_model_id": openai_models[1],
        "is_enabled": True,
    },
    models=[
        {
            "id": "pmod_openai_1",
            "source": "catalog",
            "model_id": openai_models[0],
            "display_name": None,
            "enabled": True,
            "sort_order": 0,
        },
        {
            "id": "pmod_openai_2",
            "source": "catalog",
            "model_id": openai_models[1],
            "display_name": None,
            "enabled": True,
            "sort_order": 1,
        },
    ],
)

repos.providers.save_bundle(
    connection_data={
        "id": "prov_custom_demo",
        "kind": "custom",
        "runtime_provider": "custom",
        "catalog_provider_id": None,
        "custom_slug": "demo-gateway",
        "display_name": "Demo Gateway",
        "base_url": "https://llm.example.com/v1",
        "secret_ref": "sec_prov_custom_demo",
        "api_key_configured": True,
        "model_policy": "custom_only",
        "preferred_model_id": "demo-model-alpha",
        "is_enabled": True,
    },
    models=[
        {
            "id": "pmod_custom_1",
            "source": "custom",
            "model_id": "demo-model-alpha",
            "display_name": "Demo Model Alpha",
            "enabled": True,
            "sort_order": 0,
        },
        {
            "id": "pmod_custom_2",
            "source": "custom",
            "model_id": "demo-model-beta",
            "display_name": "Demo Model Beta",
            "enabled": True,
            "sort_order": 1,
        },
    ],
)

store.save_provider_secret(
    "sec_prov_openai_demo",
    ProviderSecretPayload(version=1, api_key="sk-openai-demo"),
)
store.save_provider_secret(
    "sec_prov_custom_demo",
    ProviderSecretPayload(version=1, api_key="sk-custom-demo"),
)

db.close()

print(
    json.dumps(
        {
            "openai_model_1": openai_models[0],
            "openai_model_2": openai_models[1],
        },
        ensure_ascii=False,
    )
)
PY
)"

OPENAI_MODEL_1="$(${PY_RUN[@]} -c 'import json,sys; print(json.loads(sys.argv[1])["openai_model_1"])' "$SEED_JSON")"
OPENAI_MODEL_2="$(${PY_RUN[@]} -c 'import json,sys; print(json.loads(sys.argv[1])["openai_model_2"])' "$SEED_JSON")"

run_cli() {
  echo
  echo "==> $*"
  ${PY_RUN[@]} -m nsbot_sidecar.cli --ns-bot-home "$NS_BOT_HOME" "$@"
}

echo
echo "[*] Seeded demo providers with models:"
echo "    - OpenAI: $OPENAI_MODEL_1, $OPENAI_MODEL_2"
echo "    - Custom: demo-model-alpha, demo-model-beta"

run_cli providers list
run_cli models status
run_cli models list --provider openai
run_cli run "diagnose default selection" --diagnose

echo
echo "[*] providers use openai (without --model, should auto pick first model)"
run_cli providers use openai
run_cli models status
run_cli run "diagnose after providers use" --diagnose

echo
echo "[*] Disable one builtin model then list OpenAI models"
run_cli models disable --connection-id prov_openai_demo --model "$OPENAI_MODEL_2"
run_cli models list --connection-id prov_openai_demo
run_cli run "diagnose explicit connection" --diagnose --connection-id prov_openai_demo --selected-model-id "$OPENAI_MODEL_1"

echo
echo "[*] Remove one custom model then inspect custom models"
run_cli models remove --connection-id prov_custom_demo --model demo-model-alpha
run_cli models list --connection-id prov_custom_demo

echo
echo "[*] Delete custom provider connection and verify"
run_cli providers delete --connection-id prov_custom_demo
run_cli providers list

echo
echo "[OK] CLI demo finished."
