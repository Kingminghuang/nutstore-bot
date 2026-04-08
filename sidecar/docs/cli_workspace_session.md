## CLI workspace/session quick start

### Workspace commands

```bash
# List workspaces
uv run python src/cli.py workspaces list

# Create workspace
uv run python src/cli.py workspaces create \
  --name demo \
  --real-path /absolute/path/to/workspace

# Update workspace metadata
uv run python src/cli.py workspaces update \
  --workspace-id ws_xxx \
  --name "new name" \
  --path-label "/visible/path"

# Check sidecar index status
uv run python src/cli.py workspaces sidecar-index-status --workspace-id ws_xxx

# Delete workspace
uv run python src/cli.py workspaces delete --workspace-id ws_xxx
```

### Session commands

```bash
# List sessions by workspace
uv run python src/cli.py sessions list --workspace-id ws_xxx

# Create session (optionally pin connection/model)
uv run python src/cli.py sessions create \
  --workspace-id ws_xxx \
  --connection-id prov_xxx \
  --model-id openai/gpt-5.4

# Rename session
uv run python src/cli.py sessions update \
  --session-id sess_xxx \
  --title "new title"

# Read timeline
uv run python src/cli.py sessions timeline --session-id sess_xxx --limit 50

# Delete session
uv run python src/cli.py sessions delete --session-id sess_xxx
```

### Run with persisted session

When `--session-id` is provided, CLI resolves `session_key` and workspace path from DB,
then executes runtime in that session context.

```bash
uv run python src/cli.py run "your prompt" --session-id sess_xxx --diagnose
```
