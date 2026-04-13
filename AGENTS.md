# Repository Guidelines

## Project Structure & Module Organization
- `frontend/`: Vite + React + TypeScript UI. Main app code is under `frontend/src/` with feature slices in `features/`, shared utilities in `shared/`, and app entry in `app/page.tsx`.
- `sidecar/`: Python 3.11+ FastAPI service (`src/nsbot_sidecar/`) with layered modules: `api/`, `application/`, `domain/`, `infrastructure/`, and `runtime/`.
- `src-tauri/`: Rust desktop shell for packaging and runtime orchestration.
- `scripts/`: macOS desktop build/smoke scripts. `templates/` contains runtime template resources.

## Build, Test, and Development Commands
- Frontend dev with sidecar (recommended): `cd frontend && npm run dev:with-sidecar`
- Frontend only: `cd frontend && npm run dev`
- Frontend production build: `cd frontend && npm run build`
- Frontend preview: `cd frontend && npm run start`
- Frontend tests: `cd frontend && npm test`
- Frontend lint: `cd frontend && npm run lint`
- Sidecar setup: `cd sidecar && uv sync`
- Sidecar API locally: `cd sidecar && uv run python -m nsbot_sidecar.api.api_server`
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

## Runtime Architecture Guardrails
- Runtime call sites (`RunService`, `sidecar/src/nsbot_sidecar/cli.py`, and `sidecar/src/nsbot_sidecar/runtime/worker.py`) must use the `nsbot_sidecar.runtime.engine` interface and must not directly instantiate `AgentRuntimeService`.
- Keep `execute_runtime_run` as a compatibility entry point, but keep it as a thin forwarder to RuntimeEngine.
- During Phase 0 style refactors, preserve external behavior: API contract unchanged, worker stdin/stdout JSON unchanged, and SSE event names remain `run.*`.
- For runtime-layer changes, run at minimum:
  - `cd sidecar && uv run pytest tests/test_runtime_service.py tests/test_worker.py tests/test_api_server.py`
- When adding a new runtime backend, prefer injecting via `runtime_engine_factory` instead of branching at business call sites.

## Phase 1 Runtime Conventions
- Runtime kernel baseline is `ToolCallingAgent` (main) + managed `CodeAgent` (`python_exec_agent`) for on-demand Python execution.
- Main-agent tool priority is a default strategy (not a hard requirement): prefer `read -> grep -> find -> ls`, and only use `edit/write` after sufficient evidence is collected.
- Managed `CodeAgent` is a fallback path for tasks that cannot be completed efficiently or reliably with standard workspace tools (`read/grep/find/ls`), including but not limited to computation, data transformation, and script-style workflows.
- Preserve Phase 1 external behavior: API contract unchanged, worker stdin/stdout JSON unchanged, and SSE event names remain `run.*`.
- For runtime/tooling changes, run at minimum:
  - `cd sidecar && uv run pytest tests/test_runtime_service.py tests/test_worker.py tests/test_api_server.py tests/test_tools.py`
- In `sidecar/src/nsbot_sidecar/runtime/tools.py`, keep tool metadata unambiguous:
  - no duplicate keys in `inputs`;
  - include practical default/range semantics in parameter descriptions where relevant.
