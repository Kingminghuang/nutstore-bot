## CLI workspace/thread quick start

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

### Thread commands

```bash
# List recent threads
uv run python -m nsbot_sidecar.cli threads list

# Read thread details
uv run python -m nsbot_sidecar.cli threads get --thread-id thread_xxx

# Rename thread
uv run python -m nsbot_sidecar.cli threads update \
  --thread-id thread_xxx \
  --title "new title"

# Delete thread
uv run python -m nsbot_sidecar.cli threads delete --thread-id thread_xxx
```

### Run with persisted thread

When `--thread-id` is provided, agent run resolves thread session metadata and workspace
path from DB, then executes runtime in that existing thread context.

```bash
uv run python -m nsbot_sidecar.cli agent run --prompt "your prompt" --thread-id thread_xxx
```
