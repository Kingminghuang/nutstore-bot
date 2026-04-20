# Repository Guidelines

## Project Structure & Module Organization
- `frontend/`: Vite + React + TypeScript UI. Main app code is under `frontend/src/` with feature slices in `features/`, shared utilities in `shared/`, and app entry in `app/page.tsx`.
- `sidecar/`: Python 3.11+ ACP stdio sidecar (`src/nsbot_sidecar/`) with layered modules: `api/`, `application/`, `domain/`, `infrastructure/`, and `runtime/`.
- `src-tauri/`: Rust desktop shell for packaging and runtime orchestration.
- `scripts/`: macOS desktop build/smoke scripts. `templates/` contains runtime template resources.

## Build, Test, and Development Commands
- Desktop dev (recommended): `cd frontend && npm run tauri dev`
- Frontend only (UI-only, no ACP desktop bridge): `cd frontend && npm run dev`
- Frontend production build: `cd frontend && npm run build`
- Frontend preview: `cd frontend && npm run start`
- Frontend tests: `cd frontend && npm test`
- Frontend lint: `cd frontend && npm run lint`
- Sidecar setup: `cd sidecar && uv sync`
- Sidecar ACP stdio locally: `cd sidecar && uv run python -m nsbot_sidecar.api.acp_stdio`
- Sidecar tests: `cd sidecar && uv run pytest`
- macOS desktop build: `bash ./scripts/build-desktop-macos.sh` (add `--dmg` or `--debug` as needed)

## Coding Style & Naming Conventions
- TypeScript/React: 2-space indentation, functional components, `PascalCase` for components, `camelCase` for functions/variables, and colocated `*.test.ts(x)` for unit tests.
- Python: PEP 8 style, 4-space indentation, `snake_case` modules/functions, explicit type hints where practical.
- Rust: follow `rustfmt` defaults and idiomatic `snake_case`/`CamelCase` naming.
- Run lint/tests before opening a PR; keep imports and dead code clean.

## Testing Guidelines
- Frontend uses Vitest + Testing Library. Name tests `*.test.ts` or `*.test.tsx`.
- Sidecar uses Pytest with tests under `sidecar/tests/`.
- Add or update tests for any behavior change, especially API contract changes and session/runtime flows.
- Distinguish **in-process app tests** from **real runtime/transport smoke tests**. `fastapi.testclient.TestClient` validates ASGI app behavior, but it does not validate Uvicorn startup wiring, import-path issues, protocol upgrade handling, or optional websocket backend availability.
- For sidecar features that depend on server startup, HTTP upgrade, optional dependencies, or packaging/import behavior, add at least one test that goes through the real entrypoint and a real client transport instead of only `TestClient` or patched unit tests.

## Migration Closure Policy (No Dribble)
- Skip budget is zero by default: target test suites should not end with `skipped` tests.
- Do not use temporary evasion patterns (renaming to `legacy_*`, long-term `skip`, or commenting out failing tests) as a migration endpoint.
- When removing/replacing an interface (for example `/runs*`), complete in one delivery:
  1) new interface implementation,
  2) equivalent test coverage for new interface,
  3) old-interface test removal,
  4) green run with no new skips.
- Build an explicit mapping of `old test -> replacement test` before deleting legacy tests.
- Definition of Done for migration/refactor work:
  - required test commands pass,
  - no failing tests,
  - no newly introduced skips.
- If full replacement cannot be completed promptly, escalate early with blockers and options instead of continuing incremental partial work.
- Final report for migration work must include `passed/failed/skipped` counts.

## Commit & Pull Request Guidelines
- Follow existing history style: imperative subject lines, often Conventional Commit prefixes (`feat:`, `fix:`, `refactor:`).
- Keep commits focused and logically grouped.
- PRs should include: purpose, key changes, test evidence (`npm test`, `uv run pytest`), and linked issues.
- UI-impacting changes should include screenshots or short recordings.

## Security & Configuration Tips
- Do not commit secrets or local runtime state.
- Validate sidecar/provider flows against local `NS_BOT_HOME` data, and redact sensitive fields in logs and error messages.

## Harness Guide
- When designing solutions and writing code, there is no need to consider any compatibility or migration issues; functionality can be implemented entirely according to one's own vision. One is free to utilize the latest language features and libraries without any restrictions. This maximizes creativity and efficiency, enabling the rapid achievement of objectives.
- Do not design or implement database migrations, compatibility layers, backward-compatibility shims, or transitional fallback paths unless the user explicitly overrides this rule for a specific task.

## Runtime Architecture Guardrails
- Runtime call sites (`sidecar/src/nsbot_sidecar/api/acp_session.py`, `sidecar/src/nsbot_sidecar/cli.py`, and `sidecar/src/nsbot_sidecar/runtime/worker.py`) must use the `nsbot_sidecar.runtime.engine` interface.
- Keep `execute_runtime_turn` as the thin application entry point to RuntimeEngine.
- Runtime interaction is ACP-only over stdio (desktop path: Frontend IPC -> Tauri bridge -> sidecar stdio JSON-RPC). Do not add or restore `/runs*` endpoints, `run.*` event streams, HTTP `edit-and-run` style paths, or frontend-facing ACP websocket routes.
- `sidecar` has no standalone HTTP server surface. Any new business capability must go through ACP methods, not REST.
- For transport-layer changes, do not stop at in-process app tests; verify real stdio ACP handshake and request/notification flow through the bridge.
- For runtime-layer changes, run at minimum:
  - `cd sidecar && uv run pytest tests/test_runtime_engine.py tests/test_worker.py tests/test_acp_stdio.py`

## ACP Hard-Cut Rules
- **No semantic leftovers after decoupling**: if transport/protocol is no longer websocket-based, remove websocket-named files/symbols and dead helpers in the same delivery (no long-lived `*_ws` misnomers).
- **No dormant compatibility flags**: do not keep "temporary" toggles for removed transports/paths unless explicitly approved with an expiry condition.
- **Dependency feasibility first**: if a plan depends on a new package (for example ACP SDK), first validate install/import viability (`uv add` + import smoke check) before large refactors.
- **Single history source**: conversation history and edit anchors must be event-native (`acp_event_log`, `eventId`); do not reintroduce `timeline_entries`/`entryId` compatibility aliases.
- **Load contract clarity**: keep `session/load` as "attach/load session state only" and use `timeline/list` for historical replay.

## ACP Permission Policy
- Default client policy is `auto-allow=true`.
- Permission interception is limited to controlled actions: `write`, `edit`, and `python_exec_agent`.
- Read-class tools (`read/grep/find/ls` and similar) must pass without permission prompts.
- Only when `auto-allow=false`, agent emits `session/request_permission` and blocks until client response.
- On `session/cancel`, all pending permission requests in that session must converge to `cancelled` and unblock waiting execution.

## Frontend Runtime State Model
- Frontend runtime UI state should be session/turn-based, not run-id based.
- Do not introduce new `activeRunId`/`sessionRunStatusById` style state.
- Loading indicators should derive from “current turn” semantics (entries after the latest `user_input`) plus session pending status.

## Phase 1 Runtime Conventions
- Runtime kernel baseline is `ToolCallingAgent` (main) + managed `CodeAgent` (`python_exec_agent`) for on-demand Python execution.
- Main-agent tool priority is a default strategy (not a hard requirement): prefer `read -> grep -> find -> ls`, and only use `edit/write` after sufficient evidence is collected.
- Managed `CodeAgent` is a fallback path for tasks that cannot be completed efficiently or reliably with standard workspace tools (`read/grep/find/ls`), including but not limited to computation, data transformation, and script-style workflows.
- For runtime/tooling changes, run at minimum:
  - `cd sidecar && uv run pytest tests/test_runtime_engine.py tests/test_worker.py tests/test_acp_stdio.py tests/test_tools.py`
- In `sidecar/src/nsbot_sidecar/runtime/tools.py`, keep tool metadata unambiguous:
  - no duplicate keys in `inputs`;
  - include practical default/range semantics in parameter descriptions where relevant.

## Execution Hygiene
- Use `apply_patch` for code edits; do not invoke `apply_patch` through `exec_command`.
- For frontend tests in this repo, run `npm test` directly (do not append unsupported flags like `--runInBand`).
