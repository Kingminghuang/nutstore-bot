# Frontend + Sidecar Local-Service Checklist

## Decisions

- Frontend remains the UI layer.
- Sidecar becomes the local application server.
- Sidecar exposes a local FastAPI HTTP service on `127.0.0.1`.
- Frontend communicates with sidecar over local HTTP, and later SSE for streaming.
- Provider catalog comes from `sidecar/provider_catalog.py`.
- Provider connections persist inside sidecar using `SQLite + local encrypted files`.
- `ModelSelector` shows models grouped by configured provider connection, and only for providers with configured keys.
- Session titles use a two-step strategy: heuristic title immediately, model-generated title asynchronously.
- Session timestamps come from persisted backend data, not frontend placeholders.
- Product direction is local-first, so local process lifecycle, localhost auth, and workspace path trust are first-class concerns.

## Architecture

### Runtime Shape

- `frontend` is a local UI client.
- `sidecar` is a local FastAPI service.
- `sidecar` continues to own runtime execution via `runtime_service.py`.
- Provider persistence, workspace persistence, session persistence, and run orchestration all move into sidecar.

### Communication

- Frontend -> sidecar: HTTP JSON for request/response APIs.
- Frontend -> sidecar: SSE for streaming run events in a later phase.
- Sidecar internal execution: direct Python service calls, not a separate Node bridge.
- `worker.py` can remain temporarily for CLI/testing compatibility, but is no longer the main integration path.

### Local Service Rules

- Bind sidecar only to `127.0.0.1`.
- Introduce a local auth token for all mutating or sensitive endpoints.
- Add a discovery file for actual port/token if dynamic port allocation is used.
- Keep workspace path resolution and trusted filesystem access entirely inside sidecar.

## Does Sidecar Need Changes?

Yes. Sidecar now becomes the primary persistence and API layer.

Current sidecar already provides the runtime core:

- provider catalog source: `sidecar/provider_catalog.py`
- built-in vs custom base URL behavior: `sidecar/direct_model.py` and `sidecar/runtime_service.py`
- session memory/runtime execution core: `sidecar/session_manager.py` and `sidecar/runtime_service.py`
- legacy process bridge entrypoint: `sidecar/worker.py`

But sidecar still needs new capabilities:

- FastAPI HTTP server
- SQLite storage and migrations
- local encrypted secret storage
- HTTP APIs for providers, model options, workspaces, sessions, messages, and runs
- localhost auth and service discovery
- optional SSE streaming endpoints

## Recommended Sidecar Layout

```text
sidecar/
  api_server.py
  api_models.py
  storage.py
  repositories.py
  secret_store.py
  auth.py
  discovery.py
  services/
    provider_service.py
    workspace_service.py
    session_service.py
    run_service.py
```

Existing modules to keep and reuse:

- `sidecar/provider_catalog.py`
- `sidecar/direct_model.py`
- `sidecar/runtime_service.py`
- `sidecar/session_manager.py`
- `sidecar/worker.py` as transitional/legacy entrypoint

## Provider Catalog Changes

`sidecar/provider_catalog.py` should become the single source of truth for UI-facing provider metadata.

Recommended additions to each provider payload:

- `runtimeProvider`
- `kind`
- `baseUrlPolicy`

Recommended `baseUrlPolicy` values:

- `hidden` for `anthropic`, `deepseek`, `gemini`
- `optional` for `openai`
- `required` for custom OpenAI-compatible providers exposed by the sidecar API

## SQLite Schema

```sql
CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  path_label TEXT NOT NULL,
  real_path TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE provider_connections (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('builtin', 'custom')),
  runtime_provider TEXT NOT NULL CHECK (
    runtime_provider IN ('anthropic', 'deepseek', 'gemini', 'openai', 'custom')
  ),
  catalog_provider_id TEXT,
  custom_slug TEXT,
  display_name TEXT NOT NULL,
  base_url TEXT,
  secret_ref TEXT NOT NULL,
  api_key_configured INTEGER NOT NULL DEFAULT 0,
  model_policy TEXT NOT NULL DEFAULT 'all_catalog' CHECK (
    model_policy IN ('all_catalog', 'restricted', 'custom_only')
  ),
  preferred_model_id TEXT,
  is_enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_builtin_provider_connection
ON provider_connections(catalog_provider_id)
WHERE kind = 'builtin';

CREATE TABLE provider_models (
  id TEXT PRIMARY KEY,
  connection_id TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('catalog', 'custom')),
  model_id TEXT NOT NULL,
  display_name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (connection_id) REFERENCES provider_connections(id) ON DELETE CASCADE
);

CREATE INDEX idx_provider_models_connection
ON provider_models(connection_id, sort_order, model_id);

CREATE TABLE provider_headers (
  id TEXT PRIMARY KEY,
  connection_id TEXT NOT NULL,
  name TEXT NOT NULL,
  value_kind TEXT NOT NULL CHECK (value_kind IN ('plain', 'secret')),
  plain_value TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (connection_id) REFERENCES provider_connections(id) ON DELETE CASCADE
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  session_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  title_source TEXT NOT NULL CHECK (
    title_source IN ('placeholder', 'heuristic', 'model', 'manual')
  ),
  title_status TEXT NOT NULL DEFAULT 'idle' CHECK (
    title_status IN ('idle', 'pending', 'ready', 'failed')
  ),
  title_generation_attempts INTEGER NOT NULL DEFAULT 0,
  last_message_preview TEXT,
  message_count INTEGER NOT NULL DEFAULT 0,
  active_connection_id TEXT,
  active_model_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_message_at TEXT,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY (active_connection_id) REFERENCES provider_connections(id)
);

CREATE INDEX idx_sessions_workspace_updated
ON sessions(workspace_id, updated_at DESC);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_id TEXT,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  step_id TEXT,
  sequence_no INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_messages_session_sequence
ON messages(session_id, sequence_no);

CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  connection_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
  ),
  input_text TEXT NOT NULL,
  final_answer TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY (connection_id) REFERENCES provider_connections(id)
);
```

## Secret Storage

- Database path: `~/.nsbot/sidecar.db`
- Secret directory: `~/.nsbot/secrets/`
- Master key path: `~/.nsbot/master.key`
- Encryption: `AES-256-GCM`
- One encrypted file per provider connection: `sec_<connection_id>.enc`
- Write encrypted files atomically: write temp file, then rename

Encrypted file payload after decryption:

```json
{
  "version": 1,
  "apiKey": "sk-xxx",
  "secretHeaders": {
    "hdr_abc": "secret-value"
  }
}
```

## Provider Rules

- Built-in providers come from `sidecar/provider_catalog.py`
- Built-in IDs must match sidecar exactly: `anthropic`, `deepseek`, `gemini`, `openai`
- Do not keep the current frontend-only `google` alias
- `baseUrl` rules:
  - `anthropic` / `deepseek` / `gemini`: hidden
  - `openai`: optional
  - `custom`: required

## Sidecar API Contract

### `GET /health`

Returns service health and version information.

```json
{
  "ok": true,
  "service": "sidecar",
  "version": "0.1.0"
}
```

### `GET /provider-catalog`

Returns sidecar-owned catalog data.

```json
{
  "providers": [
    {
      "id": "openai",
      "label": "OpenAI / Compatible",
      "kind": "builtin",
      "runtimeProvider": "openai",
      "baseUrlPolicy": "optional",
      "models": [
        {
          "id": "gpt-5.4",
          "supportsReasoningTokens": true,
          "reasoningEffortValues": ["none", "low", "medium", "high", "xhigh"]
        }
      ]
    },
    {
      "id": "custom",
      "label": "Custom OpenAI-Compatible",
      "kind": "custom-template",
      "runtimeProvider": "custom",
      "baseUrlPolicy": "required",
      "models": []
    }
  ]
}
```

### `GET /providers`

Returns persisted provider connections, with secrets redacted.

```json
{
  "connections": [
    {
      "id": "prov_openai",
      "kind": "builtin",
      "runtimeProvider": "openai",
      "catalogProviderId": "openai",
      "displayName": "OpenAI",
      "baseUrl": null,
      "apiKeyConfigured": true,
      "modelPolicy": "all_catalog",
      "preferredModelId": "gpt-5.4",
      "enabledModelIds": [],
      "customModels": [],
      "headers": [],
      "updatedAt": "2026-03-24T12:00:00Z"
    }
  ]
}
```

### `POST /providers`

Built-in provider example:

```json
{
  "kind": "builtin",
  "catalogProviderId": "openai",
  "displayName": "OpenAI",
  "baseUrl": null,
  "apiKey": "sk-xxx",
  "modelPolicy": "all_catalog",
  "preferredModelId": "gpt-5.4",
  "enabledModelIds": []
}
```

Custom provider example:

```json
{
  "kind": "custom",
  "customSlug": "my-company",
  "displayName": "My Company Gateway",
  "baseUrl": "https://llm.example.com/v1",
  "apiKey": "sk-xxx",
  "customModels": [
    {
      "modelId": "my-model",
      "displayName": "My Model"
    }
  ],
  "headers": [
    {
      "name": "X-Tenant",
      "valueKind": "plain",
      "plainValue": "team-a"
    },
    {
      "name": "X-Token",
      "valueKind": "secret",
      "secretValue": "secret-123"
    }
  ]
}
```

### `PATCH /providers/{id}`

- Omit `apiKey` to keep the old key
- Send `apiKey: null` to remove the key
- Send `apiKey: "..."` to replace the key
- Apply the same semantics to secret headers

### `DELETE /providers/{id}`

- Delete DB rows
- Delete encrypted secret file

### `GET /model-options`

Returns grouped model options for `ModelSelector`.

```json
{
  "groups": [
    {
      "connectionId": "prov_openai",
      "providerLabel": "OpenAI",
      "providerId": "openai",
      "models": [
        {
          "modelId": "gpt-5.4",
          "label": "gpt-5.4",
          "supportsReasoningTokens": true,
          "reasoningEffortValues": ["none", "low", "medium", "high", "xhigh"]
        }
      ]
    }
  ],
  "defaultSelection": {
    "connectionId": "prov_openai",
    "modelId": "gpt-5.4"
  }
}
```

Rules:

- Only include enabled provider connections with configured keys
- Built-in provider groups use sidecar catalog models
- Custom provider groups use persisted custom models

### `GET /workspaces`

Returns registered local workspaces.

### `POST /workspaces`

Registers a trusted local workspace.

```json
{
  "name": "nutstore-bot",
  "realPath": "C:\\repo\\nutstore-bot",
  "pathLabel": "C:\\repo\\nutstore-bot"
}
```

### `GET /workspaces/{workspaceId}/sessions`

```json
{
  "sessions": [
    {
      "id": "sess_001",
      "workspaceId": "ws_001",
      "title": "Refactor provider persistence",
      "titleSource": "model",
      "createdAt": "2026-03-24T12:00:00Z",
      "updatedAt": "2026-03-24T12:10:00Z",
      "lastMessageAt": "2026-03-24T12:10:00Z",
      "messageCount": 6,
      "lastMessagePreview": "I’ll split provider catalog from persisted connections..."
    }
  ]
}
```

### `POST /workspaces/{workspaceId}/sessions`

```json
{
  "connectionId": "prov_openai",
  "modelId": "gpt-5.4"
}
```

Response:

```json
{
  "id": "sess_001",
  "workspaceId": "ws_001",
  "title": "New session",
  "titleSource": "placeholder",
  "createdAt": "2026-03-24T12:00:00Z",
  "updatedAt": "2026-03-24T12:00:00Z",
  "lastMessageAt": null,
  "messageCount": 0
}
```

### `PATCH /sessions/{id}`

```json
{
  "title": "Provider config persistence"
}
```

- When a user edits the title, set `titleSource = manual`
- Never auto-overwrite manual titles

### `GET /sessions/{id}/messages`

Returns persisted session messages.

### `POST /runs`

Frontend request:

```json
{
  "sessionId": "sess_001",
  "workspaceId": "ws_001",
  "connectionId": "prov_openai",
  "modelId": "gpt-5.4",
  "input": "把 frontend 和 sidecar 串起来"
}
```

Internal sidecar runtime request mapping:

```json
{
  "run_id": "run_001",
  "user_input": "把 frontend 和 sidecar 串起来",
  "auth_context": {
    "uid": "local-user",
    "tid": "local-team",
    "exp_epoch": 0
  },
  "metadata": {
    "workspace_path": "C:\\real\\workspace",
    "session_key": "sess_001"
  },
  "config": {
    "model_id": "gpt-5.4",
    "direct_provider": "openai",
    "direct_base_url": "",
    "direct_api_key": "sk-xxx",
    "direct_model_id": "gpt-5.4"
  }
}
```

### `GET /runs/{id}/events`

Reserved for later SSE streaming.

Proposed SSE event envelope:

```json
{
  "id": "run_001:7",
  "event": "run.step",
  "data": {
    "type": "run.step",
    "runId": "run_001",
    "sessionId": "sess_001",
    "sequence": 7,
    "createdAt": "2026-03-24T12:00:05Z",
    "stepId": "step-2",
    "stepKind": "action",
    "modelOutput": "Searching workspace",
    "observations": ["Found 6 matches"],
    "error": null,
    "usage": {
      "inputTokens": 120,
      "outputTokens": 48,
      "reasoningTokens": 0
    },
    "durationMs": 320,
    "hasDelta": true
  }
}
```

Planned event types:

- `run.status` for queued/running/completed/failed/cancelled transitions
- `run.delta` for token/text chunks tied to a `stepId`
- `run.step` for normalized planning/action step snapshots
- `run.message` for persisted user/assistant/system messages
- `run.completed` for final answer completion
- `run.failed` for terminal failures
- `run.keepalive` for idle heartbeat frames
- `run.replay-ready` when persisted replay state is fully catch-up ready

### `POST /runs/{id}/cancel`

Reserved for later cooperative cancellation.

## Frontend Type Refactor

Replace the current `ProviderConfig` / `ConnectedProvider` frontend-only model with separate catalog, connection, model-option, and session types.

```ts
export type ProviderCatalogModel = {
  id: string
  supportsReasoningTokens: boolean
  reasoningEffortValues?: string[]
}

export type ProviderCatalogEntry = {
  id: string
  label: string
  kind: "builtin" | "custom-template"
  runtimeProvider: "anthropic" | "deepseek" | "gemini" | "openai" | "custom"
  baseUrlPolicy: "hidden" | "optional" | "required"
  models: ProviderCatalogModel[]
}

export type ProviderHeaderDraft = {
  id: string
  name: string
  valueKind: "plain" | "secret"
  plainValue: string
  secretValueInput: string
  hasStoredSecret: boolean
}

export type ProviderModelDraft = {
  id: string
  modelId: string
  displayName: string
  source: "catalog" | "custom"
  enabled: boolean
}

export type ProviderConnectionSummary = {
  id: string
  kind: "builtin" | "custom"
  runtimeProvider: "anthropic" | "deepseek" | "gemini" | "openai" | "custom"
  catalogProviderId?: string
  displayName: string
  baseUrl: string | null
  apiKeyConfigured: boolean
  preferredModelId: string | null
  enabledModelIds: string[]
  updatedAt: string
}

export type ProviderConnectionDetail = ProviderConnectionSummary & {
  customSlug?: string
  modelPolicy: "all_catalog" | "restricted" | "custom_only"
  customModels: ProviderModelDraft[]
  headers: ProviderHeaderDraft[]
}

export type ModelOption = {
  connectionId: string
  providerLabel: string
  providerId: string
  modelId: string
  label: string
  supportsReasoningTokens: boolean
  reasoningEffortValues?: string[]
}

export type ModelOptionGroup = {
  connectionId: string
  providerLabel: string
  providerId: string
  models: ModelOption[]
}

export type SelectedModelRef = {
  connectionId: string
  modelId: string
}

export type SessionSummary = {
  id: string
  workspaceId: string
  title: string
  titleSource: "placeholder" | "heuristic" | "model" | "manual"
  createdAt: string
  updatedAt: string
  lastMessageAt: string | null
  messageCount: number
  lastMessagePreview: string | null
}
```

## ModelSelector Data Flow

1. Frontend loads `GET /model-options` from sidecar
2. Sidecar loads provider catalog and persisted provider connections
3. Sidecar filters out providers without configured keys
4. Sidecar builds grouped model options
5. Frontend stores `selectedModel` as `{ connectionId, modelId }`
6. Frontend sends `connectionId + modelId` with each run request
7. Sidecar resolves the connection, decrypts secrets, and builds the runtime config

UI rules:

- Show provider groups, not a flat model list
- Group label should use the connection display name
- Disable the selector when no configured providers exist
- Disable submit when no model selection is available

## Session Title and Time Design

### Time

- Persist real timestamps in sidecar tables
- Use `updatedAt` or `lastMessageAt` for sidebar sorting and display
- Frontend formats relative time locally
- Remove the current placeholder values like `now` and `just now`

### Title

- On session creation:
  - `title = "New session"`
  - `titleSource = "placeholder"`
- After the first user message:
  - immediately set a heuristic title from the first user prompt
  - `titleSource = "heuristic"`
- After the first assistant response completes:
  - enqueue a background title-generation task
  - if successful, replace the heuristic title
  - `titleSource = "model"`
- If the user manually edits the title:
  - set `titleSource = "manual"`
  - never auto-overwrite again

Title generation is best-effort and must not block the chat response.

Suggested prompt contract for title generation:

- input: first user message + first assistant answer excerpt
- output: 5-10 words
- no punctuation-heavy output
- avoid generic prefixes like `Help with` or `Question about`

## Local Service Discovery and Auth

- Default port: `8765`
- If the port is unavailable, sidecar may choose a fallback port
- Sidecar writes discovery info to a local file such as `~/.nsbot/service.json`
- Discovery file should include at least:
  - `port`
  - `baseUrl`
  - `token`
  - `pid`
- Frontend startup flow should read discovery info before issuing API requests
- Sensitive endpoints should require `Authorization: Bearer <token>`

Discovery file example:

```json
{
  "baseUrl": "http://127.0.0.1:8765",
  "port": 8765,
  "token": "local-secret-token",
  "pid": 12345
}
```

## FastAPI Lifespan Note

Current sidecar service foundation uses `@app.on_event("shutdown")` to close the SQLite connection. This works, but FastAPI now marks `on_event` as deprecated in favor of lifespan handlers.

Recommended follow-up:

- Replace `@app.on_event("shutdown")` in `sidecar/api_server.py` with a FastAPI lifespan function.
- Move sidecar startup/shutdown resource management into the lifespan block:
  - open database connection
  - initialize repositories/services
  - publish service discovery
  - close database connection on shutdown
- Keep endpoint behavior unchanged while removing the deprecation warning.

Suggested direction:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    db = connect_database(...)
    app.state.database = db
    try:
        yield
    finally:
        db.close()
```

## Cross-Platform Local Paths

Current implementation can use `NS_BOT_HOME` or fall back to `~/.nsbot`, which works cross-platform. For productization, local storage paths should follow platform conventions where possible.

Recommended resolution order:

1. `NS_BOT_HOME`
2. platform-standard app data directory
3. fallback `~/.nsbot`

Recommended target layout:

- Windows:
  - root: `%AppData%/NutstoreBot`
  - discovery: `%AppData%/NutstoreBot/service.json`
  - database: `%AppData%/NutstoreBot/sidecar.db`
  - secrets: `%AppData%/NutstoreBot/secrets/`
- macOS:
  - root: `~/Library/Application Support/NutstoreBot`
  - discovery: `~/Library/Application Support/NutstoreBot/service.json`
  - database: `~/Library/Application Support/NutstoreBot/sidecar.db`
  - secrets: `~/Library/Application Support/NutstoreBot/secrets/`
- Linux:
  - root: `$XDG_STATE_HOME/NutstoreBot` or `$XDG_CONFIG_HOME/NutstoreBot`
  - discovery: `$XDG_STATE_HOME/NutstoreBot/service.json`
  - database: `$XDG_STATE_HOME/NutstoreBot/sidecar.db`
  - secrets: `$XDG_STATE_HOME/NutstoreBot/secrets/`

Implementation notes:

- Keep `NS_BOT_HOME` as the highest-priority override for development and testing.
- Use a single resolved root for discovery, database, secrets, and future logs.
- Discovery, DB, and secrets should always live under the same resolved root to simplify backup and recovery.
- Add migration logic later if the app moves from `~/.nsbot` to platform-standard directories.

## Implementation Checklist

### Phase 0: Reset Earlier Node-Bridge Prototype

- [x] Remove temporary Node bridge implementation from `frontend/lib/server`
- [x] Remove temporary frontend `test:bridge` script and related Node bridge artifacts
- [x] Rewrite the plan around sidecar-first local HTTP service

### Phase 1: Sidecar Service Foundation

- [x] Add FastAPI and uvicorn dependencies to `sidecar/pyproject.toml`
- [x] Create `sidecar/api_server.py` with FastAPI app bootstrap
- [x] Add `GET /health`
- [x] Add localhost-only bind policy
- [x] Add local auth token generation and validation
- [x] Add service discovery file write/read helpers
- [x] Add a sidecar startup command for local server mode
- [x] Add tests for health endpoint, auth, and discovery behavior

Current local server startup command:

```bash
uv run python api_server.py
```

### Phase 2: Sidecar Storage Layer

- [x] Add SQLite initialization and migrations inside sidecar
- [x] Add repository layer for workspaces, providers, sessions, messages, and runs
- [x] Add master key bootstrap for `~/.nsbot/master.key`
- [x] Add encrypted file read/write helpers for provider secrets
- [x] Add tests for DB migration and encrypted secret persistence

### Phase 3: Provider Catalog and Persistence

- [x] Extend `sidecar/provider_catalog.py` output with `runtimeProvider`, `kind`, and `baseUrlPolicy`
- [x] Add tests for enriched provider catalog payload
- [x] Implement `GET /provider-catalog`
- [x] Implement `GET /providers`
- [x] Implement `POST /providers`
- [x] Implement `PATCH /providers/{id}`
- [x] Implement `DELETE /providers/{id}`
- [x] Implement validation rules for built-in vs custom provider `baseUrl`
- [x] Ensure provider secrets never round-trip back to frontend in plaintext
- [x] Add integration tests for built-in provider persistence
- [x] Add integration tests for custom provider persistence

### Phase 4: ModelSelector

- [x] Implement `GET /model-options`
- [x] Filter out provider connections without configured keys
- [x] Use sidecar catalog for built-in provider model groups
- [x] Use persisted custom models for custom provider model groups
- [x] Replace hardcoded provider list in `frontend/components/settings-modal.tsx`
- [x] Replace hardcoded model list in `frontend/components/main-content.tsx`
- [x] Replace the `google` frontend alias with `gemini`
- [x] Refactor `selectedModel` from `string` to `{ connectionId, modelId }`
- [x] Disable `ModelSelector` and submit when no configured providers exist
- [x] Add frontend tests for grouped selector rendering and default selection

### Phase 5: Workspaces, Sessions, and Titles

- [x] Implement `GET /workspaces`
- [x] Implement `POST /workspaces`
- [x] Add trusted workspace path registration rules
- [x] Add session repository methods for create/list/update/title state
- [x] Use `session.id` as `session_key` when calling runtime service
- [x] Implement `GET /workspaces/{workspaceId}/sessions`
- [x] Implement `POST /workspaces/{workspaceId}/sessions`
- [x] Implement `PATCH /sessions/{id}` for manual renaming
- [x] Implement `GET /sessions/{id}/messages`
- [x] Replace frontend placeholder `time` strings with persisted timestamps
- [x] Replace frontend placeholder session titles with backend-driven titles
- [x] Add heuristic title generation after the first user message
- [x] Add async model title generation after the first completed run
- [x] Ensure manual titles are never overwritten
- [x] Add tests for placeholder -> heuristic -> model title transitions

### Phase 6: Run Orchestration

- [x] Implement `POST /runs`
- [x] Resolve workspace path from `workspaceId`
- [x] Resolve provider connection and decrypt provider secrets
- [x] Build runtime request payload from `connectionId + modelId`
- [x] Call `runtime_service.py` directly from sidecar services
- [x] Persist user and assistant messages into sidecar tables
- [x] Persist run status and final answer
- [x] Replace `MOCK_RESPONSES` in `frontend/app/page.tsx`
- [x] Replace frontend in-memory provider/session truth with API-driven state
- [x] Add integration tests for sidecar run request mapping

### Phase 7: Frontend Refactor

- [x] Replace `frontend/lib/provider-settings.ts` with the new split type model
- [x] Refactor `frontend/components/settings-modal.tsx` to use sidecar APIs
- [x] Refactor `frontend/components/main-content.tsx` to use grouped model options
- [x] Refactor `frontend/app/page.tsx` to fetch providers, sessions, and model options from sidecar
- [x] Replace local `ConnectedProvider[]` state with API-driven provider connection state
- [x] Replace local session summary state with API-driven session summary state
- [x] Keep transient UI-only state local: modal open state, dropdown open state, input text, drag state

### Phase 8: Local Product Integration

- [x] Decide how frontend starts sidecar locally
- [x] Decide how frontend reads sidecar discovery info
- [x] Decide how trusted local workspace selection works in the product shell
- [x] Verify frontend can reach sidecar on localhost with auth token
- [x] Verify sidecar lifecycle works across app restart

### Phase 9: Streaming and Cancellation

- [x] Design SSE event protocol for run updates
- [x] Implement `GET /runs/{id}/events` or equivalent SSE endpoint
- [x] Stream deltas, steps, and final completion events to frontend
- [x] Implement `POST /runs/{id}/cancel`
- [x] Add cooperative cancellation from API layer to runtime execution
- [x] Add tests for streaming and cancellation behavior

### Phase 10: Verification

- [x] Verify a built-in OpenAI connection can be saved without `baseUrl`
- [x] Verify a Gemini connection does not show `baseUrl` in the UI
- [x] Verify a custom provider requires `baseUrl`
- [x] Verify only providers with configured keys appear in `ModelSelector`
- [x] Verify model groups match provider connections, not frontend hardcoded arrays
- [x] Verify session list survives refresh/restart
- [x] Verify session title becomes heuristic after the first user message
- [x] Verify session title can upgrade to a model-generated title asynchronously
- [x] Verify manual title edits are preserved
- [x] Verify sidecar builds a valid runtime request for built-in and custom providers
- [x] Verify localhost auth blocks unauthorized requests

### Phase 11: Post-MVP Enhancements

- [x] Add `POST /providers/{id}/validate` to test provider config and selected model before saving or enabling
- [x] Add provider health/status fields so the UI can show `connected`, `invalid key`, `timeout`, or `model unavailable`
- [x] Add secret rotation flows for API keys and secret headers, including stale secret cleanup when connections are deleted or downgraded
- [x] Add catalog refresh/versioning so sidecar can reconcile stored preferred models after catalog updates
- [x] Keep one saved connection per built-in provider; do not add multiple saved connections because the current product does not need multi-account or multi-environment coexistence
- [x] Add end-to-end `reasoningEffort` support through frontend, API models, runtime config, and direct model plumbing
- [x] Add persisted run step history so complete planning/action steps can be replayed in the UI after refresh, without restoring delta streaming
- [x] Add title-generation failure fallback that uses the first user message snippet (up to 50 chars)
- [ ] Add session history pagination and lazy message loading for large conversations
- [ ] Add session summary or memory metadata in sidecar to complement runtime session history for faster sidebar rendering
- [ ] Add attachment persistence and upload lifecycle if the composer's file picker should survive refreshes
- [ ] Add import/export tooling for provider connections, ideally without exporting plaintext secrets by default
- [x] Add audit logging for provider updates, session renames, and run failures to simplify debugging
- [ ] Add recovery tooling for corrupted SQLite rows or missing encrypted secret files

## Current Frontend Hardcoded Data To Replace

- `frontend/components/settings-modal.tsx`: hardcoded provider list
- `frontend/components/main-content.tsx`: hardcoded model list
- `frontend/app/page.tsx`: hardcoded mock assistant replies
- `frontend/app/page.tsx`: placeholder session title/time values

## Out of Scope For This Milestone

- Full multi-user auth
- Cross-device secret sync
- Rich attachment upload pipeline
- Separate low-cost title model routing
