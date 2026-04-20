### Sidecar Runtime / ACP 重构计划（按当前实现校准）

**状态图例**
- `[x]` 已完成
- `[~]` 部分完成 / 已有落地但未完全收口
- `[ ]` 未开始

**当前结论**
- `[x]` Runtime 抽象已经落地：sidecar 已有 `RuntimeEngine` 协议、`RuntimeWorkerConfig`/`RuntimeResult` 等统一 DTO，`turn_service.py`、`worker.py`、CLI 入口均通过 `create_runtime_engine(...)` 调用 runtime，而不是继续依赖旧的 `AgentRuntimeService`。
- `[x]` Agent 内核替换已经落地：当前 runtime 主代理已经是 `NativeToolCallingAgent`，并挂载 `NativeCodeAgent` 作为 managed subagent，名称为 `python_exec_agent`。
- `[x]` ACP 最小可用已经落地，而且实现面已超过最小集：除 `initialize`、`session/new`、`session/prompt`、`session/update`、`session/cancel` 外，还支持 `session/load`、`session/list`、`session/resume`、`session/set_mode`、`session/set_config_option` 以及 `_nsbot/*` 扩展方法。
- `[~]` 事件模型迁移只完成了一半：sidecar 已经能生成 `thread.started / turn.started / item.* / turn.completed / turn.failed` 风格事件，但它们目前主要是由 CLI 基于 runtime/timeline 数据投影得到；底层 runtime 仍输出 `delta` 与 `timeline_entry`，持久化层也仍以 ACP event log / timeline 为主，而不是“ThreadEventType 直接替换到底”。
- `[ ]` agent-os 外部沙箱尚未落地：仓库内没有 `SandboxAdapter`、`AgentOsSandboxAdapter`、独立 bridge service 或 agent-os backend 接入实现。
- `[x]` `runtime_service.py` 已删除，仓库内也无对应文件残留。

### 实际落地情况（逐项校准）
- `1) NativeCodeAgent(CodeAgent) -> ToolCallingAgent + Python Code 子代理`：`[x]` 已完成。
  - 当前 `runtime/engine.py` 直接创建 `NativeToolCallingAgent`，并把 `NativeCodeAgent(..., name="python_exec_agent")` 作为 `managed_agents=[code_agent]` 传入。
  - 已有测试覆盖该编排关系、日志配置和额外工具注入。
- `2) 强化 CLI 并去除 runtime_service.py`：`[x]` 已完成。
  - `runtime/types.py` 已提供 `RuntimeEngine` 协议。
  - `application/turn_service.py` 只保留薄入口 `execute_runtime_turn(...)`。
  - `runtime/worker.py` 和 CLI 都通过 `create_runtime_engine(...)` 调用 runtime。
  - `runtime/runtime_service.py` 已不存在。
- `3) agent loop 使用 agent-os 外部沙箱`：`[ ]` 未开始。
  - 当前仍使用本地 sandbox / 本地工具链，没有 agent-os bridge、RPC/HTTP adapter、也没有 backend 选择层。
- `4) MultiStepAgent 输出适配为 ThreadEventType`：`[~]` 部分完成。
  - CLI 已有 `_build_codex_thread_events(...)`，可把 runtime 事件投影成 `thread.started / turn.started / item.started / item.updated / item.completed / turn.completed / turn.failed`。
  - 但 runtime 内核仍然产生 `delta` 和 `timeline_entry`；timeline service 仍基于 `acp_event_log` 提供 `_nsbot/timeline/list`；代码里还明确写了 timeline deprecation notice，说明仍处于兼容收口阶段。
  - 这意味着“新事件可用”，但“旧表示被彻底替换”还没有完成。
- `5) sidecar 支持 ACP`：`[x]` 已完成。
  - 已有 `api/acp_stdio.py` stdio 入口。
  - 已有 `AcpJsonRpcSession` 处理 initialize/auth/session 生命周期、prompt、cancel、permission 请求与 session/update 推送。
  - 已有 `tests/test_acp_stdio.py` 做 stdio 集成测试与 CLI `--acp` 回路测试。

### 分阶段状态
1. **Phase 0: Runtime 抽象先行（不改行为）** `[x]`
- `RuntimeEngine`、`RuntimeWorkerConfig`、`RuntimeEventStream` 等基础抽象已存在。
- `turn_service.py`、`worker.py`、CLI 均已基于 runtime engine 编排。
- 原文里“CLI、API RunService、worker 都直接依赖 AgentRuntimeService”的前提已经过时。

2. **Phase 1: Agent 内核替换（CodeAgent -> ToolCallingAgent + Code 子代理）** `[x]`
- 当前实现已经不是“计划中的新引擎”，而是直接在 `SmolagentsRuntimeEngine` 内完成了主/子代理组装。
- `python_exec_agent` 的职责也已经按约束收窄为 Python 执行 fallback。

3. **Phase 2: agent-os 外部沙箱接入** `[ ]`
- 尚未看到 `SandboxAdapter` 抽象。
- 尚未看到 agent-os bridge service。
- 尚未看到 runtime backend `local/agent-os` 选择能力。

4. **Phase 3: 事件模型切到 ThreadEventType** `[~]`
- 已完成的部分：CLI/线程接口已经能消费 Codex SDK 风格事件；thread view 侧的投影逻辑已经存在。
- 未完成的部分：runtime 原生事件、持久化事件、timeline/list 合同、session/update 存储格式还没有统一到单一 thread event 模型。
- 当前真实状态更接近“ACP timeline 为主，thread events 为派生投影”。

5. **Phase 4: ACP 最小可用实现** `[x]`
- 已超过“最小可用”。
- 已实现 stdio server、SDK transport bridge、prompt/update/cancel、load/list/resume、权限请求、session 配置切换等能力。

6. **Phase 5: 删除 runtime_service 与收口** `[x]`
- `runtime_service.py` 已删除。
- 旧 runtime service 入口未再发现引用。
- 但“整体收口”仍受 Phase 3 未完成影响：虽然 service 文件已删，事件模型仍未完全收敛。

### 当前实现与原方案的主要偏差
- 原方案假设 `run.*` 仍是主事件模型，这个前提已不准确。当前 sidecar 内部更多是 `delta + timeline_entry + ACP session/update + thread event 投影` 的混合状态。
- 原方案假设 ACP 还只是首版最小面，这也已不准确；当前 ACP 已经是 sidecar 的核心外部协议面之一。
- 原方案假设要新建 `ToolCallingRuntimeEngine`。实际实现没有单独拆新类，而是在现有 `SmolagentsRuntimeEngine` 中完成了主代理切换。
- 原方案假设需要保留 runtime_service 的薄兼容层过渡；实际代码已经直接删除该文件。

### 剩余工作清单（按当前代码重排）
- `[ ]` 完成 Phase 2：引入 runtime backend 抽象与 agent-os 外部服务接入。
- `[ ]` 完成 Phase 3：把 runtime 原生事件、持久化存储、timeline/load 合同、线程回放统一到单一事件模型，去掉派生投影与兼容 notice。
- `[ ]` 收尾验证：在 Phase 3 完成后，补一轮端到端测试，确认 thread 实时流、历史回放、取消、权限请求、edit-and-prompt 的合同一致。

### 建议验收口径（基于当前现状）
- Runtime 内核替换验收：`tests/test_runtime_engine.py` 覆盖主代理为 `ToolCallingAgent`、托管 `python_exec_agent`、工具注入与日志行为。
- ACP 验收：`tests/test_acp_stdio.py` 覆盖 stdio handshake、session/update 流和 CLI `--acp` 回路。
- 事件模型收口验收：后续应补“同一 turn 的实时事件与历史回放完全等价”的测试，而不是只验证 CLI 投影函数。

### 现阶段默认判断
- `agent-os 外部服务`：仍是后续方向，但当前仓库尚未开始实现。
- `ThreadEventType 直接替换`：尚未完成，当前是兼容投影态，不应再在文档中表述为既定事实。
- `ACP 最小可用优先`：已完成，并已向更完整的会话协议面延伸。
