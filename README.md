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
