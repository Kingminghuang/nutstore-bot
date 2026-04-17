## Plan: ACP Conversation Runtime Refactor

以 AionUi 的 ACP 分层为蓝本，重建当前前端的会话运行时：把“发送/建 session/接收 session/update/权限请求/turn 结束 hydration/ConversationStream 渲染”从页面级散乱状态，收敛成统一的 session-turn runtime 和 message adapter。重点目标是两件事立即成立：用户点击发送后，用户消息和新 session 行必须先于 `session/new`、`session/set_config_option`、`session/prompt` 的完成而出现；同时把 ConversationStream 的流式消息组合、工具调用卡片、thinking、permission、end-of-turn cleanup 迁移到更接近 AionUi 的模式。保持 sidecar ACP 合同不变，不做兼容层，不延续旧 run-stream 心智。

**Steps**
1. Phase 1 - Runtime boundary and state model: 在 [frontend/src/app/page.tsx](frontend/src/app/page.tsx) 现有状态流基础上，抽离统一的 ACP session runtime，明确三类状态：`persisted session summary/history`、`client-only draft/pending session`、`live turn stream state`。这一步先定义状态边界、生命周期和 ownership，替代当前把 `sessionsByWorkspace`、`activeSessionId`、`pendingSessionId`、`liveTurnBySession`、permission 状态分散维护的方式。
2. Phase 1 - Temp session identity: 为“草稿模式首次发送”引入客户端临时 session 标识和 workspace-scoped draft turn。用户点击发送时立即创建临时 session row 和 optimistic user message，不等待 `session/new` 返回；待 `session/new` 成功后把临时 session 原子地 rebind 到真实 `sessionId`，并迁移 attachments、live turn、active selection、permission 上下文。此步依赖步骤 1。
3. Phase 1 - Send pipeline rewrite: 重写发送链路，使 `handleSendMessage` 从“先请求、后显示”改为“先本地提交 turn，再异步获取/绑定服务端 session，再 prompt”。`session/set_config_option`、`session/prompt` 只推进 runtime 状态，不负责首次可见性。此步依赖步骤 2。
4. Phase 2 - ACP adapter/composer: 以 AionUi 的 AcpAdapter/compose 思路重做消息组合层，替换 [frontend/src/app/live-turn-state.ts](frontend/src/app/live-turn-state.ts)、[frontend/src/features/conversation/ui/messages/normalize-messages.ts](frontend/src/features/conversation/ui/messages/normalize-messages.ts) 目前的 append-only 合并方式。目标是把 timeline events 与 live notifications 统一编排为稳定的 render model，按稳定键合并 `assistant message chunk`、`thinking chunk`、`tool_call`、`tool_call_update`、`plan`、`available_commands`、`permission`，避免碎片化尾部拼接。
5. Phase 2 - Turn guards and cleanup: 引入 turn lifecycle guard，显式区分 `turn started / streaming / waiting permission / finished / cancelled / failed / hydrating`，并在 turn 终止时统一做 cleanup 和 hydration 触发，避免当前依赖 `pendingSessionId` + 局部状态清理的脆弱模式。此步与步骤 4 强相关，建议一并完成。
6. Phase 2 - Permission integration: 把 [frontend/src/app/use-permission-requests.ts](frontend/src/app/use-permission-requests.ts) 并入新的 runtime 语义中，确保 permission request 绑定到当前 session/turn/tool-call，并在 temp session -> real session rebind 时不丢失状态。此步可与步骤 5 并行推进，但在接口上依赖步骤 1。
7. Phase 3 - ConversationStream rewrite: 让 [frontend/src/features/conversation/ui/conversation-stream.tsx](frontend/src/features/conversation/ui/conversation-stream.tsx) 和 [frontend/src/features/conversation/ui/messages/message-list.tsx](frontend/src/features/conversation/ui/messages/message-list.tsx) 从“渲染 timeline_entry + live tail”改成“渲染 composed conversation stream”。ConversationStream 不再理解 persisted/live 的来源差异，只消费统一消息流、scroll policy 和 pagination contract。此步依赖步骤 4。
8. Phase 3 - UI parity lift: 参照 AionUi 的呈现逻辑，重新组织 thinking、assistant draft、tool call、permission、step/loading 卡片的视觉与交互优先级，减少当前把 live plan/tool/thinking/assistant 机械串接的结构。此步依赖步骤 7，可与消息子组件改造并行。
9. Phase 3 - Sidebar/session list coherence: 重写 sidebar 所消费的 session collection，使它既能展示 persisted sessions，也能展示本地 pending/draft sessions，并在 hydration lag 或 `listWorkspaceSessions` 暂时未返回新 session 时保持可见，不再由服务端列表决定“新 session 是否存在”。此步依赖步骤 2，可与步骤 7 并行。
10. Phase 4 - Hydration and pagination contract: 保留 sidecar 的 `session/load` + `_nsbot/timeline/list` 合同，但把 hydration 逻辑收敛进 runtime：首次载入历史、turn 完成后的 reload、edit-and-rerun 的 `truncatedAfterSequence`、load-earlier 的 cursor 都由 runtime 提供统一 API。此步依赖步骤 4 和步骤 9。
11. Phase 4 - Test replacement and expansion: 更新 [frontend/src/app/page.test.tsx](frontend/src/app/page.test.tsx)、[frontend/src/features/conversation/ui/messages/normalize-messages.test.ts](frontend/src/features/conversation/ui/messages/normalize-messages.test.ts)、[frontend/src/features/conversation/ui/messages/message-list.test.tsx](frontend/src/features/conversation/ui/messages/message-list.test.tsx) 以及 ConversationStream 相关测试，覆盖新的 runtime/composer 行为。重点验证：点击发送瞬间用户消息出现、首次发送瞬间 session row 出现、temp->real session rebind 无闪烁、hydration lag 不丢 session、tool_call_update 合并、permission 阻塞恢复、edit-and-rerun 截断回放、load earlier 保持滚动位置。此步依赖前述实现完成。

**Relevant files**
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/app/page.tsx` — 当前 ACP 初始化、session/new、session/prompt、notification 消费、hydration、sidebar/main-content 装配都集中在这里；这里是主要拆分入口。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/app/live-turn-state.ts` — 当前 optimistic session/message、live turn 和 merge 逻辑过于薄弱；应被 runtime/composer 新模型替代或大幅改写。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/app/use-permission-requests.ts` — 现有 permission 状态与 liveTurn 绑定过浅，需纳入新的 turn lifecycle。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/features/conversation/ui/main-content.tsx` — 当前持有过多 conversation 交互与编辑状态，并负责把 timeline/liveTurn 变成 UI；需要改为消费统一 runtime 输出。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/features/conversation/ui/conversation-stream.tsx` — 当前仅是滚动容器 + list；重构后应成为纯 conversation stream 渲染层。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/features/conversation/ui/messages/normalize-messages.ts` — 当前 live message 只是顺序追加；这里是迁移到 AionUi 风格 adapter/composer 的直接替换点。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/features/conversation/ui/messages/message-list.tsx` — 当前按简单 `type` 分发；重构后应消费更稳定的 composed message model。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/shared/api/sidecar/sidecar-client.ts` — 复用 `session/load`、`_nsbot/timeline/list`、workspace session list 和 timeline projection；必要时补充 runtime 需要的更明确 helper，但不改 sidecar 合同。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/features/session/types/index.ts` — session/liveTurn/pending permission 类型会成为 runtime 的基础协议，需要重定义以支持 temp session 和 richer turn state。
- `/Users/huangqingming/Workspace/nutstore-bot/frontend/src/app/page.test.tsx` — 已有部分 optimistic/session lag 测试，可作为重构后行为回归的骨架，但要按新 runtime 模型重写断言。

**Verification**
1. 前端单测覆盖以下关键路径：首次发送时不等待 `session/new` 就看到 user bubble；首次发送时 sidebar 立即出现新 session；`session/new` 返回后 temp session 被替换为真实 session 且 active session、attachments、permission、stream 不丢失。
2. 验证 `session/update` 流处理：`agent_message_chunk`、`agent_thought_chunk`、`tool_call`、`tool_call_update`、`plan`、`available_commands_update`、`session/request_permission` 都能在统一 message stream 中稳定合并，并在 hydration 后正确落盘。
3. 验证 `edit_and_prompt`：从某个 `eventId` 截断后，旧尾部不再显示，新的 optimistic user input 和新 turn 流正确接续。
4. 验证分页：`Load earlier messages` 拉取更老 timeline 时保留滚动位置，且不会污染当前 live turn。
5. 运行 `cd frontend && npm test`，重点关注 page、conversation message、stream 相关测试；必要时补充新的 runtime/composer 单测，避免只依赖页面集成测试。

**Decisions**
- 保持 sidecar ACP 接口不变：继续使用 `session/new`、`session/load`、`session/prompt`、`session/request_permission`、`_nsbot/timeline/list`、`_nsbot/session/edit_and_prompt`。
- 不考虑兼容性：本次直接以 AionUi 风格重建前端 session-turn runtime 和 ConversationStream，不保留旧 run-stream 或旧状态组织方式。
- 重点是 ConversationStream，但实现范围必须覆盖发送链路、session list、permission、hydration，因为这几者与 UI 可见性是同一问题链条。
- 不在本次范围内重做 provider settings、workspace CRUD 交互、右侧设计预留面板；仅在它们与会话 runtime 接口相连处做必要调整。

**Further Considerations**
1. 推荐把“消息组合层”和“渲染层”严格分离：adapter/composer 只产出稳定消息模型，组件层不再直接读取 raw `liveTurn` 字段。
2. 推荐把 temp session 作为正式状态一等公民，而不是在 `activeDraftWorkspaceId` 上继续叠加特判；否则首次发送与后续 turn 仍会分裂成两套路径。
3. 推荐将 hydration 失败建模为 runtime 状态而不是直接回退到页面错误文案，这样 ConversationStream 可以维持 optimistic turn 的连续性.
