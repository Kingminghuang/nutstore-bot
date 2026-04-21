## CLI workspace/thread quick start

## Provider/model storage and CLI semantics

This section is the current source of truth for sidecar provider/model persistence.
If implementation and older notes disagree, follow this document and the current
code in `storage.py`, `provider_service.py`, and the `nsbot.cli` package.

### Storage model

Provider/model persistence no longer uses the old single denormalized `models`
 table. The current schema is:

- `providers`
  - `id`: provider identifier used by sidecar
  - `runtime_provider`: runtime backend name such as `openai`, `anthropic`, or `custom`
  - `catalog_provider_id`: builtin catalog provider id, or `NULL` for custom providers
  - `display_name`: user-visible provider label
  - `base_url`: custom provider base URL, or builtin override when applicable
  - `secret_ref`: key used to load/store the provider secret payload
  - `preferred_model_id`: provider-local fallback/default model
  - `created_at`, `updated_at`
- `models`
  - `id`: row id for a persisted custom model
  - `provider_id`: owning provider id
  - `model_id`: runtime model identifier
  - `display_name`: user-visible label for custom models
  - `created_at`, `updated_at`
- `default_model_selection`
  - single-row table with `id = 1`
  - `provider_id`, `model_id`: global default model selection across all providers
  - `created_at`, `updated_at`

Important invariants:

- There is exactly one global default model selection at most.
- `providers.preferred_model_id` is provider-local fallback state, not the global default.
- Builtin provider catalog models are not persisted row-by-row in `models`; they are read from `provider_catalog` at runtime.
- `models` is used for custom provider models only.
- `providers` and `models` intentionally do not use a database foreign key.
- Secrets are not stored in the SQLite provider/model tables; only `secret_ref` is persisted.

### Removed legacy behavior

The following old assumptions are no longer valid and should not be reintroduced:

- No single denormalized provider/model table.
- No `providers use` command.
- No per-provider enabled/disabled persistence for models.
- No persisted `kind`, `custom_slug`, `api_key_configured`, `model_policy`, `is_enabled`, `source`, `enabled`, or `sort_order` columns.
- No restricted builtin model subsets persisted in SQLite.
- No provider-local `set-default` behavior that acts as a global default.

### CLI command semantics

#### `providers list`

- Returns the provider catalog and configured providers.
- Configured providers are loaded from `providers`.
- For builtin providers, available models come from the runtime catalog, not the `models` table.

#### `providers delete --provider-id <id>`

- Deletes the provider row from `providers`.
- Deletes matching custom model rows from `models`.
- Deletes the provider secret referenced by `secret_ref`.
- Clears `default_model_selection` if it points at the deleted provider.

#### `models list [--provider-id <id>]`

- Returns model groups from `provider_service.model_options_payload()`.
- Builtin provider model lists are materialized from the catalog.
- Custom provider model lists are materialized from the `models` table.
- The returned `defaultSelection` is resolved in this order:
  1. `default_model_selection`
  2. `providers.preferred_model_id`
  3. first available model in the first available group

#### `models create --name <provider> --base-url <url> --model-id <model> --api-key <key>`

- Creates or updates a custom OpenAI-compatible provider.
- If the provider does not exist:
  - inserts a new `providers` row
  - inserts the first custom model into `models`
  - sets `providers.preferred_model_id` to the created model
- If the provider already exists and is custom:
  - appends a new row to `models`
  - preserves the existing `preferred_model_id`
- If the provider already exists and is builtin, the command fails.

#### `models get <provider_id:model_id>`

- Resolves the identity against the current runtime-visible model options.
- Returns provider metadata plus the resolved provider/model tuple.

#### `models set-default <provider_id:model_id>`

- Resolves the identity against the current runtime-visible model options.
- Writes the global default to `default_model_selection`.
- Does not update every provider row and does not mean “use this provider”.
- The selected model may belong to any provider; the default is globally unique.

#### `models remove --model <model> [--provider-id <id>]`

- Only supported for custom providers.
- Deletes the matching row from `models`.
- If the removed model was `providers.preferred_model_id`, rewrites that field to the next remaining custom model or `NULL`.
- Clears `default_model_selection` if it points at the removed model.
- When `--provider-id` is omitted, CLI resolves the provider from the current model option graph and requires the model id to be unique enough to disambiguate.

### ACP stdio mode

Use the root-level `--acp` flag when the CLI should run as an ACP stdio server:

```bash
uv run python -m nsbot.cli --acp
```

Notes:

- `--acp` is a top-level mode switch and should not be combined with subcommands.
- `run` remains the one-shot execution path; `--acp` keeps the process alive for ACP JSON-RPC over stdio.

### Workspace commands

```bash
# List workspaces
uv run python -m nsbot.cli workspaces list

# Create workspace
uv run python -m nsbot.cli workspaces create \
  --name demo \
  --real-path /absolute/path/to/workspace

# Update workspace metadata
uv run python -m nsbot.cli workspaces update \
  --workspace-id ws_xxx \
  --name "new name" \
  --path-label "/visible/path"

# Check workspace index status
uv run python -m nsbot.cli workspaces index status --workspace-id ws_xxx

# Delete workspace
uv run python -m nsbot.cli workspaces delete --workspace-id ws_xxx
```

### Thread commands

```bash
# List recent threads
uv run python -m nsbot.cli threads list

# Read thread details
uv run python -m nsbot.cli threads get --thread-id thread_xxx

# Rename thread
uv run python -m nsbot.cli threads update \
  --thread-id thread_xxx \
  --title "new title"

# Delete thread
uv run python -m nsbot.cli threads delete --thread-id thread_xxx
```

### Run with persisted thread

When `--thread-id` is provided, agent run resolves thread session metadata and workspace
path from DB, then executes runtime in that existing thread context.

```bash
uv run python -m nsbot.cli agent run --prompt "your prompt" --thread-id thread_xxx
```
