# nutstore-bot

本项目由两个子系统组成：

- `frontend`：Vite + React 前端应用（通过 Tauri IPC 与 ACP bridge 通信）
- `sidecar`：Python 本地 agent 进程（ACP JSON-RPC 2.0，stdio 传输）

## 环境要求

- Node.js `>= 20.9`
- npm
- Python `>= 3.11`
- `uv`（用于 sidecar 依赖管理与运行）

## 安装依赖

前端依赖：

```bash
cd frontend
npm install
```

sidecar 依赖：

```bash
cd sidecar
uv sync
```

安全说明（sidecar）：

- Provider secrets 当前存储为本地明文 JSON（`NS_BOT_HOME/secrets/*.enc`，文件后缀仅历史兼容命名）。
- 旧版本密文不会自动迁移；升级后如遇 provider 鉴权失败，请在 UI/CLI 重新填写 secret。

## 本地开发

### 桌面联调（唯一入口）

在 `frontend` 目录执行：

```bash
npm run tauri dev
```

当前仓库只支持桌面端 Tauri 联调。前端与 sidecar 的业务交互只通过 Tauri IPC 转发 ACP JSON-RPC；sidecar 对外 HTTP 仅保留 `/health`。

补充说明：

- `npm run dev` 仅用于纯 UI 预览，不包含桌面 ACP bridge。
- sidecar 生命周期由 Tauri 桌面壳托管（stdio 子进程），不再推荐手动并行启动前后端业务链路。

### ACP 会话与历史语义（硬切后）

- `session/load`：仅附着/建立会话上下文，不回放历史。
- `timeline/list`：历史事件分页查询（event-native，`events + pagination`）。
- `session/update`：唯一增量通知总线（streaming chunk / plan / tool_call / permission / available_commands）。
- `session/edit_and_prompt`：锚点参数为 `eventId`（不再使用 `entryId`）。

### CLI 管理 workspace/session

在 `sidecar` 目录可直接通过 CLI 管理工作区和会话：

```bash
uv run python -m nsbot_sidecar.cli workspaces list
uv run python -m nsbot_sidecar.cli workspaces create --name demo --real-path /path/to/project
uv run python -m nsbot_sidecar.cli sessions list --workspace-id <workspace_id>
uv run python -m nsbot_sidecar.cli sessions create --workspace-id <workspace_id>
uv run python -m nsbot_sidecar.cli sessions update --session-id <session_id> --title "重命名会话"
uv run python -m nsbot_sidecar.cli sessions delete --session-id <session_id>
```

运行任务时，可直接绑定数据库里的 session：

```bash
uv run python -m nsbot_sidecar.cli run "帮我总结这个仓库" --session-id <session_id> --diagnose
```

## Build

在 `frontend` 目录执行：

```bash
npm run build
```

如需验证生产模式启动：

```bash
npm run start
```

## Desktop Build (Tauri, macOS arm64)

当前仓库支持桌面打包（Tauri `externalBin` + Node runtime + Next standalone + Python sidecar）：

```bash
bash ./scripts/build-desktop-macos.sh
```

如需生成可安装的 macOS DMG：

```bash
bash ./scripts/build-desktop-macos.sh --dmg
```

如需生成便于排查启动失败问题的 debug 产物：

```bash
bash ./scripts/build-desktop-macos.sh --debug
```

该命令会依次执行：

1. 构建前端静态资源（`frontend/dist`）
2. 构建 Python sidecar（PyInstaller onefile，输出 `src-tauri/binaries/nsbot-sidecar-<target-triple>`）
3. 准备 `fd/rg` 与模板资源到 `src-tauri/runtime`
4. 调用 `cargo tauri build --target aarch64-apple-darwin`

运行时资源会整理到：

```text
src-tauri/runtime
```

默认构建会生成 `.app` bundle；传入 `--dmg` 时，会改为调用 `cargo tauri build --target aarch64-apple-darwin --bundles dmg`，并在以下目录生成 DMG 安装包：

```text
src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/
```

release 产物只保证 `.app` 与 `.dmg` 可运行；不保证 raw release binary 可直接运行。

`--dmg` 目前只支持 release 构建，不支持与 `--debug` 同时使用。

如果遇到 `Killed: 9`（常见于本机 shell/环境钩子导致子脚本被系统终止），请优先确认你是通过 `bash ./scripts/build-desktop-macos.sh` 执行，而不是直接双击或用其他方式启动脚本。

### 启动已打包 App（macOS）

建议在仓库根目录执行：

```bash
APP_PATH="$(pwd)/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/NutstoreBot.app"
open "$APP_PATH"
```

如需在终端查看运行日志：

```bash
APP_PATH="$(pwd)/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/NutstoreBot.app"
"$APP_PATH/Contents/MacOS/nutstore-bot-desktop"
```

不要直接运行下面这个 raw release binary：

```bash
"$(pwd)/src-tauri/target/aarch64-apple-darwin/release/nutstore-bot-desktop"
```

它缺少 `release/binaries/{next-sidecar,nsbot-sidecar}` 这一层 sidecar 入口，因此会出现类似下面的启动错误：

```text
[desktop-runtime] initial runtime start failed: failed to spawn sidecar binaries/next-sidecar: No such file or directory (os error 2)
```

旧版 bundle 中如果看到 Dock 里有单独跳动的 `node-runtime`，原因是主应用直接执行了包内的裸 Node 可执行文件。当前实现已改为通过打包进 `runtime/next-helper/` 的后台 Next helper 承载 Node 进程，避免它以独立前台应用的形式出现在 Dock 中。

如需调试启动期错误，优先运行 debug bundle 内的主程序并打开完整 Rust backtrace：

```bash
RUST_BACKTRACE=full "$(pwd)/src-tauri/target/aarch64-apple-darwin/debug/bundle/macos/NutstoreBot.app/Contents/MacOS/nutstore-bot-desktop"
```

如需直接运行 raw debug binary，也可以：

```bash
RUST_BACKTRACE=full "$(pwd)/src-tauri/target/aarch64-apple-darwin/debug/nutstore-bot-desktop"
```

前提是先通过 `bash ./scripts/build-desktop-macos.sh --debug` 让脚本同步好 bundle 内与 `target/.../debug` 下的 `binaries` 目录。这比只在 build 过程里打开 backtrace 更有帮助，因为类似 `[desktop-runtime] initial runtime start failed` 的问题发生在应用启动阶段，而不是 Rust 编译阶段。

若出现“已损坏/无法验证开发者”提示，可先清除 quarantine：

```bash
APP_PATH="$(pwd)/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/NutstoreBot.app"
xattr -dr com.apple.quarantine "$APP_PATH"
```

## 测试

前端单元测试（Vitest）：

```bash
cd frontend
npm test
```

sidecar 单元测试（Pytest）：

```bash
cd sidecar
uv run pytest
```

## Dev E2E Smoke Test（桌面联调冒烟）

1. 启动桌面联调环境

```bash
cd frontend
npm run tauri dev
```

2. 检查 sidecar 健康状态（新开终端）

```bash
curl http://127.0.0.1:18765/health
```

3. 在桌面窗口手工验证

- workspace 列表可加载
- session history 可加载
- 发送一条消息可收到 stream / tool call / permission 卡片
- edit rerun 正常工作

说明：前端不会直连 `ws://.../acp/ws`，也不会直连 sidecar 业务 REST。

## 常见问题

- `uv: command not found`：请先安装 `uv`
- 13000 端口被占用（Tauri devUrl）：释放端口后重试
- 桌面 ACP bridge 未建立：优先检查 `npm run tauri dev` 控制台和 Tauri 窗口日志
