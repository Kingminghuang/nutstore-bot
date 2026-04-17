## CLI workspace/session quick start

### ACP stdio mode

Use the root-level `--acp` flag when the CLI should run as an ACP stdio server:

```bash
uv run python -m nsbot_sidecar.cli --acp
```

Notes:

- `--acp` is a top-level mode switch and should not be combined with subcommands.
- `run` remains the one-shot execution path; `--acp` keeps the process alive for ACP JSON-RPC over stdio.

### Workspace commands

```bash
# List workspaces
uv run python -m nsbot_sidecar.cli workspaces list

# Create workspace
uv run python -m nsbot_sidecar.cli workspaces create \
  --name demo \
  --real-path /absolute/path/to/workspace

# Update workspace metadata
uv run python -m nsbot_sidecar.cli workspaces update \
  --workspace-id ws_xxx \
  --name "new name" \
  --path-label "/visible/path"

# Check sidecar index status
uv run python -m nsbot_sidecar.cli workspaces sidecar-index-status --workspace-id ws_xxx

# Delete workspace
uv run python -m nsbot_sidecar.cli workspaces delete --workspace-id ws_xxx
```

### Session commands

```bash
# List sessions by workspace
uv run python -m nsbot_sidecar.cli sessions list --workspace-id ws_xxx

# Create session (optionally pin provider/model)
uv run python -m nsbot_sidecar.cli sessions create \
  --workspace-id ws_xxx \
  --provider-id prov_xxx \
  --model-id openai/gpt-5.4

# Rename session
uv run python -m nsbot_sidecar.cli sessions update \
  --session-id sess_xxx \
  --title "new title"

# Read timeline
uv run python -m nsbot_sidecar.cli sessions timeline --session-id sess_xxx --limit 50

# Delete session
uv run python -m nsbot_sidecar.cli sessions delete --session-id sess_xxx
```

### Run with persisted session

When `--session-id` is provided, CLI resolves `session_key` and workspace path from DB,
then executes runtime in that session context.

```bash
uv run python -m nsbot_sidecar.cli run "your prompt" --session-id sess_xxx --diagnose
```
