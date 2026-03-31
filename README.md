# nutstore-bot

本项目由两个子系统组成：

- `frontend`：Next.js 前端应用（含本地代理 API 路由）
- `sidecar`：Python FastAPI 本地服务（提供会话、运行、Provider 等能力）

## 环境要求

- Node.js `>= 20.9`（Next.js 16 运行要求）
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

### 一键联调（推荐）

在 `frontend` 目录执行：

```bash
npm run dev:with-sidecar
```

该命令会同时启动：

- sidecar：`uv run python api_server.py`（默认 `127.0.0.1:8765`）
- frontend：`next dev`（默认 `localhost:3000`）

Windows 说明：

- 在 Git Bash、PowerShell、cmd 中都可以直接运行 `npm run dev:with-sidecar`
- 若 `uv` 未加入 `PATH`，请先确认 `uv --version` 可以正常执行
- sidecar 的本地数据默认写入 `%APPDATA%\NutstoreBot`；如需自定义，可设置 `NS_BOT_HOME`

### 分开启动（可选）

先启动 sidecar：

```bash
cd sidecar
uv run python api_server.py
```

Windows 补充：

- 直接在 Git Bash、PowerShell、cmd 中运行上面的命令即可，不依赖 `run_cli.sh`
- `sidecar/run_cli.sh` 是 Bash 辅助脚本，更适合类 Unix 环境
- Windows 下如需读取 `sidecar/.env` 并一键调用 CLI，可运行 `powershell -ExecutionPolicy Bypass -File .\run_cli.ps1`
- 如需指定其他 env 文件，可运行 `powershell -ExecutionPolicy Bypass -File .\run_cli.ps1 -EnvFile .\dev.env`
- 也可以直接运行 `uv run python cli.py ...`

再启动 frontend：

```bash
cd frontend
npm run dev
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

如需生成便于排查启动失败问题的 debug 产物：

```bash
bash ./scripts/build-desktop-macos.sh --debug
```

该命令会依次执行：

1. 构建 Next standalone
2. 复制当前 `node` 运行时并生成 Next launcher sidecar（`src-tauri/binaries/node-runtime-<target-triple>` 和 `src-tauri/binaries/next-sidecar-<target-triple>`）
3. 将 Next standalone 目录、`.next/static` 和 `public` 打包进 `src-tauri/runtime/next-standalone`
4. 构建 Python sidecar（PyInstaller onefile，输出 `src-tauri/binaries/nsbot-sidecar-<target-triple>`）
5. 调用 `cargo tauri build --target aarch64-apple-darwin`

当前 macOS arm64 默认 target 固定为 `node22-macos-arm64`（在 `frontend/scripts/build-next-pkg-sidecar.mjs` 中维护）。

运行时资源会整理到：

```text
src-tauri/runtime
```

`--debug` 模式下，会生成 debug bundle，并同步补齐 bundle 与 raw debug binary 所需的 sidecar 路径：

```text
src-tauri/target/aarch64-apple-darwin/debug/bundle/macos/Nutstore Bot.app
src-tauri/target/aarch64-apple-darwin/debug/bundle/macos/Nutstore Bot.app/Contents/MacOS/binaries/{next-sidecar,nsbot-sidecar}
src-tauri/target/aarch64-apple-darwin/debug/nutstore-bot-desktop
src-tauri/target/aarch64-apple-darwin/debug/binaries/{next-sidecar,nsbot-sidecar}
```

如果遇到 `Killed: 9`（常见于本机 shell/环境钩子导致子脚本被系统终止），请优先确认你是通过 `bash ./scripts/build-desktop-macos.sh` 执行，而不是直接双击或用其他方式启动脚本。

### 启动已打包 App（macOS）

建议在仓库根目录执行：

```bash
APP_PATH="$(pwd)/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Nutstore Bot.app"
open "$APP_PATH"
```

如需在终端查看运行日志：

```bash
APP_PATH="$(pwd)/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Nutstore Bot.app"
"$APP_PATH/Contents/MacOS/nutstore-bot-desktop"
```

如需调试启动期错误，优先运行 debug bundle 内的主程序并打开完整 Rust backtrace：

```bash
RUST_BACKTRACE=full "$(pwd)/src-tauri/target/aarch64-apple-darwin/debug/bundle/macos/Nutstore Bot.app/Contents/MacOS/nutstore-bot-desktop"
```

如需直接运行 raw debug binary，也可以：

```bash
RUST_BACKTRACE=full "$(pwd)/src-tauri/target/aarch64-apple-darwin/debug/nutstore-bot-desktop"
```

前提是先通过 `bash ./scripts/build-desktop-macos.sh --debug` 让脚本同步好 bundle 内与 `target/.../debug` 下的 `binaries` 目录。这比只在 build 过程里打开 backtrace 更有帮助，因为类似 `[desktop-runtime] initial runtime start failed` 的问题发生在应用启动阶段，而不是 Rust 编译阶段。

若出现“已损坏/无法验证开发者”提示，可先清除 quarantine：

```bash
APP_PATH="$(pwd)/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Nutstore Bot.app"
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

桌面运行时冒烟测试（验证 Tauri 进程能拉起 Next/Python sidecar，macOS）：

```bash
bash ./scripts/smoke-test-tauri-standalone-next-macos.sh
```

## Dev E2E Smoke Test（本地联调冒烟）

当前仓库没有内置 Playwright/Cypress 自动化 e2e 配置，`dev e2e test` 采用本地联调冒烟流程。

1) 启动联调环境

```bash
cd frontend
npm run dev:with-sidecar
```

2) 检查 sidecar 健康状态（新开终端）

```bash
curl http://127.0.0.1:8765/health
```

预期返回包含：

```json
{"ok":true,"service":"sidecar"}
```

3) 检查前端代理到 sidecar 的链路（新开终端）

```bash
curl "http://localhost:3000/api/sidecar/proxy?path=%2Fprovider-catalog"
```

预期返回 provider catalog 的 JSON（说明 Next API route -> sidecar 代理链路正常）。

4) 浏览器手工验证

- 打开 `http://localhost:3000`
- 确认页面可加载、无阻断性报错
- 执行一条核心业务路径（例如配置 Provider / 进入会话页面 / 发起一次请求）

## 常见问题

- `uv: command not found`：请先安装 `uv`
- 3000 或 8765 端口被占用：释放端口后重试
- sidecar 未启动导致前端接口失败：优先检查 `dev:with-sidecar` 终端日志
