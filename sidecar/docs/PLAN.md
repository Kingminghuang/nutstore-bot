### 重构总方案：Sidecar Runtime 内核替换与协议升级（按你确认的方向）

**Summary**
- 可行性总体结论：`1/2/4/5` 高可行，`3`（agent-os 作为核心沙箱）中高可行但需“外部服务化”落地，不建议把 agent-os 直接嵌入 Python 进程。
- 你确认的关键选择我已纳入方案：`agent-os 外部服务`、`ThreadEventType 直接替换`、`ACP 最小可用优先`。
- 最高风险在第 4 点：当前前后端大量耦合 `run.*` 事件，直接替换必须和前端/API 同步切换，不能只改 sidecar。

### 可行性评估（逐项）
- 1) `NativeCodeAgent(CodeAgent)` → `ToolCallingAgent` + Python Code 子代理：可行。
  - 依据：当前 `smolagents 1.24.0` 已支持 `ToolCallingAgent` 与 `managed_agents`（本地已安装源码可见）。
  - 关键设计：主代理用 `ToolCallingAgent`；新增 `CodeAgent` 子代理（命名如 `python_exec_agent`）仅承接“需要执行 Python 代码”的任务，避免主循环直接跑代码。
- 2) 强化 [`cli.py`](/Users/huangqingming/Workspace/nutstore-bot/sidecar/src/nsbot_sidecar/cli.py) 并去除 [`runtime_service.py`](/Users/huangqingming/Workspace/nutstore-bot/sidecar/src/nsbot_sidecar/runtime/runtime_service.py)：可行。
  - 现状：CLI、API RunService、worker 都直接依赖 `AgentRuntimeService`，要先抽象 runtime 接口再迁移调用方。
  - 建议：保留一个薄兼容层（deprecated facade）过渡 1-2 个迭代，最后删文件。
- 3) agent loop 使用 agent-os 沙箱：可行但有前置约束。
  - 必须外部服务化（你已选）：Python sidecar 通过 RPC/HTTP 调 agent-os service。
  - 风险点：agentOS 目前 Beta，且文档导航显示 `Codex Coming Soon`，所以先按“通用 session/prompt/event 沙箱”能力集成，不绑定某单一 agent persona。
- 4) MultiStepAgent 输出适配为 ThreadEventType：可行但改动面大。
  - 现状：前端与 API 事件流使用 `run.status/run.delta/run.timeline-entry/...`（大量类型与测试耦合）。
  - 你要求“直接替换”可做，但要一次性改：事件模型、SSE event name、前端 reducer、测试基线、回放存储。
- 5) sidecar 支持 ACP：可行，建议先最小协议面。
  - 首版实现：`initialize`、`session/new`、`session/prompt`、`session/update`（通知）、`session/cancel`。
  - 先复用现有 run/session 生命周期，做 ACP adapter 层，不要先重写业务层。

### 实施顺序（强约束依赖顺序）
1. **Phase 0: Runtime 抽象先行（不改行为）**
- 新建 Runtime 接口（如 `RuntimeEngine`）与统一输入输出 DTO，先把 [`cli.py`](/Users/huangqingming/Workspace/nutstore-bot/sidecar/src/nsbot_sidecar/cli.py)、[`turn_service.py`](/Users/huangqingming/Workspace/nutstore-bot/sidecar/src/nsbot_sidecar/application/turn_service.py)、[`worker.py`](/Users/huangqingming/Workspace/nutstore-bot/sidecar/src/nsbot_sidecar/runtime/worker.py) 改为依赖接口，不直接依赖 `AgentRuntimeService`。
- 验收：现有 run.* 行为与测试不变。

2. **Phase 1: Agent 内核替换（CodeAgent → ToolCallingAgent + Code 子代理）**
- 新增 `ToolCallingRuntimeEngine`，在其中组装主 `ToolCallingAgent` 与 `CodeAgent` managed subagent。
- 将工具调用与思考/工具输出映射回统一步骤事件（先仍输出旧 run.* 以便可回归验证）。
- 验收：核心任务集回归通过，工具调用成功率不低于现状。

3. **Phase 2: agent-os 外部沙箱接入**
- 实现 `SandboxAdapter` 抽象，提供 `LocalSandboxAdapter`（当前）与 `AgentOsSandboxAdapter`（新）。
- 新增独立 agent-os bridge service（Node/TS），暴露 session/prompt/events；Python 只做调用与结果归并。
- 验收：同一任务在 agent-os backend 可完整跑通，支持取消、权限事件透传。

4. **Phase 3: 事件模型直接切到 ThreadEventType（你指定）**
- 后端事件存储与 SSE 输出改为 `thread.started / turn.started / item.started / item.updated / item.completed / turn.completed / turn.failed / error`。
- 前端事件类型、流订阅、状态机、测试快照同步切换。
- 验收：前端不再依赖 run.*，线程回放与实时流一致。

5. **Phase 4: ACP 最小可用实现**
- 新增 ACP stdio server 入口（建议单独模块，如 `acp_server.py`），将 ACP method 映射到现有 session/run 能力。
- `session/prompt` 执行期间通过 `session/update` 推送分块内容与工具事件；支持 `session/cancel`。
- 验收：可被 ACP 客户端（如 Zed/CLI）完成 initialize→new→prompt→cancel 基本流程。

6. **Phase 5: 删除 runtime_service 与收口**
- 删除 [`runtime_service.py`](/Users/huangqingming/Workspace/nutstore-bot/sidecar/src/nsbot_sidecar/runtime/runtime_service.py) 与旧导出，清理引用与文档。
- 验收：仓库无 runtime_service 依赖；CLI 成为 sidecar 统一入口与编排基准。

### 接口/类型的关键变更
- Python 侧新增：`RuntimeEngine`、`SandboxAdapter`、`ThreadEventEnvelope`（替代 run event envelope）。
- API/SSE 变更：event name 从 `run.*` 改为 ThreadEventType（破坏性变更）。
- ACP 新入口：stdio JSON-RPC server（与 HTTP API 并存）。
- CLI 增强：统一 runtime backend 选择（local/agent-os）与事件输出模式（thread events）。

### 测试与验收清单
- 单元测试：
  - ToolCallingAgent + Code subagent 编排测试。
  - ThreadEventType 映射与序列号一致性测试。
  - ACP method/notification 合规测试（initialize/new/prompt/update/cancel）。
- 集成测试：
  - CLI→RunService→SSE 全链路（实时+回放）。
  - agent-os backend 冒烟（创建 session、执行、取消、权限）。
- 回归测试：
  - 现有会话持久化、timeline、cancel、失败路径、重试路径。

### 默认假设（已锁定）
- agent-os 采用外部服务模式，不嵌入 Python 进程。
- ThreadEventType 采用“直接替换”，不保留 run.* 双轨。
- ACP 首版只做最小可用能力，不包含 fs/terminal 全量扩展。
- 允许破坏式接口调整（按你仓库 AGENTS.md 的“无需兼容迁移负担”原则）。

### 参考依据
- Rivet agentOS 事件与限制文档（Beta、事件模型、运行限制）：  
  [Events](https://rivet.dev/docs/agent-os/events/) · [Limitations](https://rivet.dev/docs/agent-os/limitations)
- ACP 协议与 Python SDK：  
  [Protocol Overview](https://agentclientprotocol.com/protocol/overview) · [Schema](https://agentclientprotocol.com/protocol/schema) · [Prompt Turn](https://agentclientprotocol.com/protocol/prompt-turn) · [Python SDK](https://agentclientprotocol.github.io/python-sdk/)
