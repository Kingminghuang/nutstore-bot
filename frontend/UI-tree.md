结合当前项目 `app` 和 `components` 目录下的源码分析，当前项目实际的 UI 组件树结构如下：

```text
Home (Page)
├── Sidebar (宽度可调 / resizable, 维护 sidebarWidth 状态)
│   ├── AccentBar (顶部渐变条)
│   ├── MenuItems
│   │   └── MenuItem("New session", icon=SquarePen)
│   ├── SessionsSection
│   │   ├── SectionHeader("Sessions")
│   │   │   └── Actions: IconButton("Add a new directory", icon=FolderPlus)
│   │   │       └── AddWorkspaceDialog (点击后显示)
│   │   │           ├── Dialog(open=workspaceDialogOpen)
│   │   │           │   ├── DialogHeader
│   │   │           │   │   ├── DialogTitle("Add directory")
│   │   │           │   │   └── DialogDescription("Register a trusted local directory for sessions and runs.")
│   │   │           │   ├── WorkspaceForm (基于目录选择能力自动切换)
│   │   │           │   │   ├── (picker-supported)
│   │   │           │   │   │   ├── Button("Select directory")
│   │   │           │   │   │   ├── SelectedDirectoryPreview(name, path) (条件渲染: 已选择目录)
│   │   │           │   │   │   └── HelperText("Choose a local directory to create a workspace.") (条件渲染: 未选择目录)
│   │   │           │   │   ├── (manual-fallback)
│   │   │           │   │   │   ├── HelperText("This environment doesn't support direct folder selection. Please enter the directory name and path manually.")
│   │   │           │   │   │   ├── WorkspaceNameInput(id="workspace-name-input", label="Directory name")
│   │   │           │   │   │   └── WorkspacePathInput(id="workspace-path-input", label="Directory path")
│   │   │           │   │   └── ErrorText(workspaceDialogError, 条件渲染)
│   │   │           │   └── DialogFooter
│   │   │           │       ├── Button("Cancel", disabled=workspaceDialogSubmitting)
│   │   │           │       └── Button("Add directory" / "Adding...", disabled=workspaceDialogSubmitting, picker-supported 下未选择目录时禁用)
│   │   └── ScrollArea (垂直滚动条区域 / overflow-y-auto)
│   │       ├── ProjectGroup ("Project 1")
│   │       ├── ProjectGroup ("Project 2")
│   │       ├── ProjectGroup ("Project 3")
│   │       ├── ... (其他 Project)
│   │       └── ProjectGroup ("Project N")
│   │           ├── ProjectGroupHeader
│   │           │   ├── ExpandButton(icon=Folder / Triangle)
│   │           │   ├── ProjectLabel("Project N")
│   │           │   └── ProjectActions (Hover时显示)
│   │           │       ├── IconButton("More options", icon=MoreVertical)
│   │           │       │   └── DropdownMenu (需处理层级/Portal，避免被 Sidebar 遮挡)
│   │           │       │       ├── MenuItem("Edit name")
│   │           │       │       └── MenuItem("Remove")
│   │           │       └── IconButton("Start new session", icon=Edit)
│   │           ├── SessionItem(label="Session 1", time="the period between now and the last update")
│   │           ├── SessionItem(label="Session 2", time="the period between now and the last update")
│   │           ├── SessionItem(label="Session 3", time="the period between now and the last update")
│   │           ├── ... (其他 SessionItem)
│   │           ├── SessionItem(label="Session N", time="the period between now and the last update")
│   │           │   ├── SessionButton(title + relative time)
│   │           │   ├── SessionActions (Hover 或 focus-within 时显示)
│   │           │   │   ├── IconButton("Remove session {title}", icon=Trash2)
│   │           │   │   └── Tooltip("Remove session")
│   │           │   └── RemoveSessionDialog (点击后显示)
│   │           │       ├── AlertDialogTitle("Remove session?")
│   │           │       ├── AlertDialogDescription("This removes the session and all persisted messages, runs, and attachments from the local sidecar database.")
│   │           │       ├── AlertDialogCancel("Cancel")
│   │           │       └── AlertDialogAction("Remove session" / "Removing...")
│   │           └── ToggleMoreButton("Show more" / "Show less", 基于内部 isExpanded 状态切换)
│   ├── FooterNav
│   │   └── NavItem("Settings", icon=Settings)
│   └── ResizeHandle (右侧边界拖拽条, cursor-col-resize)
│
├── MainContent (flex-col, h-screen)
│   ├── Header (顶部固定)
│   │   └── Title({activeSessionLabel})
│   ├── MainScrollArea (主内容滚动区 / flex-1, overflow-y-auto, relative 定位)
│   │   └── MainScrollAreaLayout (horizontal split)
│   │       ├── TimelinePane (左侧主区)
│   │       │   ├── (条件渲染: 无对话记录时)
│   │       │   │   └── EmptyStateHero
│   │       │   │       ├── CodexLogo (SVG)
│   │       │   │       ├── Heading("Let's start")
│   │       │   │       └── SubHeading({activeProjectLabel})
│   │       │   ├── (条件渲染: 有对话记录时)
│   │       │   │   └── TimelineEntryList
│   │       │   │       ├── DisplayedConversation = PersistedTimelineEntries + LiveTurnOverlay
│   │       │   │       │   ├── PersistedTimelineEntries (已落库 timeline)
│   │       │   │       │   └── LiveTurnOverlay (当前 prompt turn 的本地瞬时层)
│   │       │   │       ├── TimelineEntryRow(entryKind="user_input", role="user")
│   │       │   │       │   ├── UserMessageBubble ("User message")
│   │       │   │       │   │   ├── 状态: view
│   │       │   │       │   │   │   └── BubbleContent (whitespace-pre-wrap)
│   │       │   │       │   │   ├── 状态: editing (条件渲染: isEditing)
│   │       │   │       │   │   │   └── InlineEditPanel
│   │       │   │       │   │   │       ├── EditTextarea(initial=user content)
│   │       │   │       │   │   │       └── EditActions
│   │       │   │       │   │   │           ├── Button("Cancel")
│   │       │   │       │   │   │           └── Button("Send")
│   │       │   │       │   │   └── 状态: submitting (条件渲染: isSubmittingEdit; Send disabled/loading)
│   │       │   │       │   └── BubbleActions (anchor=right-bottom, hover 或 focus-within 时显示)
│   │       │   │       │       ├── ActionButton("Copy", icon=Copy)
│   │       │   │       │       ├── ActionButton("Edit", icon=Pencil)
│   │       │   │       │       └── Tooltip("Copy" / "Edit", hover 或 focus 时显示)
│   │       │   │       ├── TimelineEntryRow(entryKind="planning", role="assistant")
│   │       │   │       │   └── PlanningStepCard
│   │       │   │       ├── TimelineEntryRow(entryKind="action", role="assistant")
│   │       │   │       │   └── ActionStepCard
│   │       │   │       │       ├── ThoughtPanel (可折叠，默认折叠；仅存在可展示 Thought 文本时渲染)
│   │       │   │       │       │   └── 提取策略: 结构化优先（JSON.thought）-> 文本回退（从 model_output 的 Thought: 段提取）
│   │       │   │       │       ├── ToolCallsPanel (默认折叠; 仅过滤后存在可展示 toolCalls 时渲染)
│   │       │   │       │       │   └── 过滤规则: name="python_interpreter" 的 tool call 不展示
│   │       │   │       │       ├── CodeActionPanel (仅有内容时渲染)
│   │       │   │       │       ├── ErrorPanel (默认折叠; 仅 `payload.error` 存在时渲染)
│   │       │   │       │       ├── ObservationsPanel (仅有内容时渲染; 保留 Show/Hide 折叠交互)
│   │       │   │       │       ├── StepFootnote
│   │       │   │       │       └── StepRunningIndicator (仅“当前轮”最新 Action 且会话处于生成中时显示)
│   │       │   │       ├── TimelineEntryRow(entryKind="final_answer", role="assistant")
│   │       │   │           ├── AgentAvatar (gradient dot)
│   │       │   │           └── AgentMessageBubble
│   │       │   │               ├── AgentMessageContent (final_answer 已实现: fenced code block 分段 + 非代码块 Markdown 渲染)
│   │       │   │               │   ├── As-Is: CodeBlock (``` fenced block -> <pre><code> 样式)
│   │       │   │               │   ├── As-Is: MarkdownPart (非代码块片段 -> react-markdown + remark-gfm)
│   │       │   │               │   ├── As-Is: 代码块片段继续沿用现有 <pre><code> 视觉样式
│   │       │   │               │   ├── As-Is: 默认不启用 raw HTML (不接入 rehype-raw)
│   │       │   │               │   └── As-Is: 外链使用 target="_blank" + rel="noreferrer noopener"
│   │       │   │               └── MessageActions (anchor=left-bottom, hover 或 focus-within 时显示)
│   │       │   │                   ├── ActionButton("Copy", icon=Copy)
│   │       │   │                   └── Tooltip("Copy", hover 或 focus 时显示)
│   │       │   │       ├── PreStepRunLoading (仅会话生成中且“当前轮”尚无 planning/action StepCard 时显示)
│   │       │   │       │   └── 隐藏条件: 首个 StepCard 出现或本轮结束
│   │       │   │       ├── LiveTurnOverlay (条件渲染: 当前 session 存在 live turn)
│   │       │   │       │   ├── OptimisticUserMessageRow (发送后立即出现；服务端收敛后移除)
│   │       │   │       │   ├── LivePlanningStepCard*
│   │       │   │       │   ├── LiveToolCallsCard
│   │       │   │       │   │   ├── ToolCallRow*(title, kind, status)
│   │       │   │       │   │   └── PermissionWaitHint("Waiting for permission", 条件渲染: waitingForPermission)
│   │       │   │       │   └── LiveAssistantDraftRow (收到 `agent_message_chunk` 时逐步增长)
│   │       │   │       └── TimelineEntryRow(entryKind="system_notice", role="system")
│   │       │   │           └── System notice bubble
│   │       │   └── ScrollToBottomButton (绝对定位 absolute bottom, icon=ArrowDown, 未到底部时显示)
│   │       └── RightSidePanels (右侧容器，可整体开关)
│   │           ├── FileTabsPanel (左栏目，可开关)
│   │           │   ├── Tabs (normal)
│   │           │   │   ├── Tabs.List
│   │           │   │   │   ├── Tabs.Trigger("context")
│   │           │   │   │   └── SortableTab* (opened file tabs)
│   │           │   │   ├── Tabs.Content("context")
│   │           │   │   └── FileTabContent(activeFileTab)
│   │           │   │       └── ScrollView
│   │           │   │           └── File viewer (Dynamic fileComponent -> UI File)
│   │           └── FileTreePanel (右栏目，可开关, workspaceRoot = activeWorkspace.realPath)
│   │               └── Tabs (pill, data-scope="filetree")
│   │                   ├── Tabs.Trigger("changes")
│   │                   ├── Tabs.Trigger("all")
│   │                   ├── Tabs.Content("changes")
│   │                   │   └── FileTree(path="", allowed=diffFiles@activeWorkspace)
│   │                   └── Tabs.Content("all")
│   │                       └── FileTree(path="", modified=diffFiles@activeWorkspace)
│   │                           └── (recursive) Collapsible + FileTree children
│   └── ComposerPanel (底部固定)
│       ├── ComposerInputShell (relative)
│       │   ├── MentionAwareInputField(placeholder="Ask for follow-up changes")
│       │   │   ├── PlainTextSegment*
│       │   │   ├── FileMentionToken* (渲染态: [file_name](file_relative_path_to_activeWorkspacePath))
│       │   │   └── Caret
│       │   ├── FileMentionPopover (条件渲染: mention session open)
│       │   │   ├── Header("Files")
│       │   │   ├── SearchHint("Type to search for files")
│       │   │   ├── MentionResultList
│       │   │   │   ├── MentionResultItem(active)
│       │   │   │   │   ├── FileName
│       │   │   │   │   └── ParentPathLabel (仅 relativeDir 非空时显示)
│       │   │   │   └── MentionResultItem*
│       │   │   ├── EmptyState("No matching files")
│       │   │   └── LoadingState("Searching files...")
│       │   └── MentionAssistText (可选: ↑/↓, Tab, Enter, Esc)
│       │   └── ComposerPermissionOverlay (条件渲染: pendingPermissionRequest != null; 覆盖整个 ComposerInputShell)
│       │       ├── Title("Permission required")
│       │       ├── Description("This run is paused before a sensitive action.")
│       │       ├── RequestSummaryCard
│       │       │   ├── PrimaryText(toolCall.title)
│       │       │   ├── Field("Kind", toolCall.kind)
│       │       │   └── Field("Request", toolCallId)
│       │       └── Actions
│       │           ├── Button("Cancel run")
│       │           ├── Button("Deny")
│       │           ├── Button("Allow always")
│       │           └── Button("Allow once")
│       └── Toolbar
│           ├── LeftActions
│           │   ├── IconButton(icon=Plus, 支持从 activeProject 所在文件夹中选取文件)
│           │   ├── ModelSelector({selectedModel}, icon=ChevronDown)
│           │   │   └── DropdownMenu(List of Models)
│           │   ├── ReasoningSelector
│           │   │   └── DropdownMenu(Auto / low / medium / high ...)
│           │   └── PermissionSelector(icon=ShieldCheck)
│           │       └── DropdownMenu("Auto-allow", "Ask first")
│           └── RightActions
│               ├── IconButton(icon=Mic)
│               └── SubmitAction (交互组件，依据输入与生成状态条件渲染，支持键盘"Enter"发送已有输入值)
│                   ├── 状态: 默认且输入为空 -> IconButton(icon=ArrowUp, disabled=true)
│                   ├── 状态: 默认且有输入值 -> IconButton(icon=ArrowUp, active=true)
│                   └── 状态: 正在生成回复中 -> IconButton(icon=Square/Stop, active=true)
│
└── SettingsModal (条件渲染: isOpen)
   ├── ModalHeader
   │   ├── BackButton(icon=ArrowLeft) (按需显示)
   │   ├── Title(当前页面名称)
   │   └── CloseButton(icon=X)
   └── ModalContent (基于 currentPage 状态)
      ├── ProvidersPage ("providers")
      │   ├── ConnectedProviders
      │   │   ├── EmptyState("No connected providers")
      │   │   └── ConnectedProviderItem("Provider N")
      │   │       ├── ClickableBody (点击后进入 ProviderConfigPage)
      │   │       │   ├── ProviderIcon
      │   │       │   ├── ProviderName
      │   │       │   ├── ProviderDescription
      │   │       │   ├── ValidationStatusBadge("Connected" / "Not validated" / 其他失败态)
      │   │       │   ├── ValidationMessage (可选, 展示最近一次校验结果摘要)
      │   │       │   └── ModelCountBadge
      │   │       └── DisconnectButton(icon=Trash2, hover 或 focus-within 时显示)
      │   ├── PopularProviders
      │   │   └── ProviderItem(name, description, btn="+ Connect" / "Edit")
      │   └── CustomProviderSection ("Configure custom provider")
      │
      ├── CustomProviderPage ("custom-provider")
      │   ├── ConfigurationInputs (Provider ID, Display Name, Base URL, API Key)
      │   ├── ModelsListConfig
      │   └── SubmitButton("Save and continue")
      │
      ├── ConnectProviderPage ("connect-provider")
      │   ├── ProviderInfo (Icon & Description)
      │   ├── Input(API key)
      │   └── SubmitButton("Connect and continue")
      │
      └── ProviderConfigPage ("provider-config")
         ├── ProviderMetaInputs
         │   ├── Input(Provider ID, custom provider 可编辑 / built-in 只读)
         │   ├── Input(Display name)
         │   └── Input(Base URL)
         ├── Input(API key)
         ├── ModelsListConfig (带有增删功能)
         ├── HeadersListConfig (带有增删功能)
         └── SubmitButton("Save provider")
```

### Phase 4 ACP Note (2026-04-13)
1. Frontend/sidecar runtime interaction is ACP WebSocket based (`/acp/ws`), and `/runs*` SSE flow has been removed.
2. Session lifecycle uses ACP methods: `initialize`, `session/new`, `session/list`, `session/load`, `session/resume`, `session/prompt`, `session/cancel`, `session/set_config_option`.
3. `mode` config option is constrained to a single value: `ask`.
4. Permission policy defaults to `autoAllow=true` on client side. Only `write` / `edit` / `python_exec_agent` execution are controlled actions.
5. When `autoAllow=false`, sidecar emits `session/request_permission` and blocks until client response (`allow` / `reject` / `cancelled`).
6. Client cancel (`session/cancel`) causes all pending permission requests in that session to converge to `cancelled`, then prompt ends with `stopReason=cancelled`.

### Permission Interaction State（新增）
1. permission 模式由 composer 内部 `PermissionSelector("Auto-allow" | "Ask first")` 控制；发送与 edit-and-rerun 都读取当前选择。
2. 仅在 `autoAllow=false` 且命中受控动作（`write` / `edit` / `python_exec_agent`）时，`Home` 页面级状态会挂起 `pendingPermissionRequest`，并在 `ComposerInputShell` 内展示 `ComposerPermissionOverlay`。
3. 权限等待期间，`LiveToolCallsCard` 额外显示 `Waiting for permission` 提示；用户一旦 Deny/Allow once/Allow always/Cancel，overlay 与等待态同时收敛。
4. 用户取消当前 prompt 时，所有 pending permission 弹窗与等待态统一清空，并以 `cancelled` 终态收敛。

### 分析总结
1. **外层布局**: 使用 `Home` 组件作为主视图。采用左右分栏的弹性布局（`flex h-screen`），左侧是**宽度可调**的 `Sidebar`（借助 ResizeHandle 拖拽更新宽度状态），右侧自适应撑满的是 `MainContent`，在需要时作为 Overlay 弹出 `SettingsModal`。
2. **状态驱动的设置面板**: `SettingsModal` 没有采用跳转页面的方式，而是通过内部维护一个 `currentPage` 状态来实现不同配置项步骤（提供商列表、自定义提供商、连接提供商、已连接提供商编辑）间的切换。已连接 provider 列表已提升到 `Home` 页面级 state，通过 props 注入 `SettingsModal`，不再由 modal 内部独立持有。
3. **复合交互的侧边栏**: 项目组通过 `hover` 状态挂载了比较多的隐藏交互（例如针对 "github" Project 的 "More options" 菜单和 "Start new session" 悬浮按钮）。**强调：类似 DropdownMenu 这样的弹出菜单需采用 Portal 等机制渲染脱离普通文档流，严格避免被 Sidebar 的边界或 `overflow` 属性遮挡。**
4. **主内容区的滚动与状态流**: `MainContent` 内部采用纵向弹性（`flex-col`），首尾的 `Header` 和 `ComposerPanel` 空间固定，中间使用 `flex-1 overflow-y-auto` 划分出独立的一块主内容滚动区域。并利用这块滚动容器的相对定位能力，放置了一个通过滚动距离判定的绝对位置悬浮“到底部”快捷按钮。消息展示层不再等价于“纯 timeline hydration”，而是 `PersistedTimelineEntries + LiveTurnOverlay` 的合并结果：用户输入先以 optimistic row 立即展示，assistant 文本/plan/tool call 在 run 过程中走 live overlay，最终再由服务端真实 timeline 收敛替换。`ModelSelector` 的候选模型仅来自**已完成校验且状态为 `connected`** 的 provider；`Not validated` 或校验失败的 provider 不会进入模型列表。
5. **Provider 管理流已闭环**: `ProvidersPage` 不再只是空状态展示，当前已支持“查看 Connected providers -> 点击进入 ProviderConfigPage 编辑 -> 断开删除”的完整管理流；`PopularProviders` 对已连接项会切换为 `Edit` 而非重复 `+ Connect`。在保存 provider 后，前端会自动对其**主模型（优先 `preferredModelId`）**发起一次真实连通性校验，校验结果会回写到 `ConnectedProviders` 的状态 badge / message，并进一步决定其模型是否可以出现在 `ModelSelector`。
6. **时间线条目动作与编辑态**: `TimelineEntryList` 采用按 `entryKind + displayRole` 的渲染分支。`UserMessageBubble` 在右下角于 hover 或 focus-within 时显示 `Copy` 与 `Edit`，点击 `Edit` 进入行内编辑态（`EditTextarea + Cancel/Send`），提交中进入 `submitting` 子状态。`AgentMessageBubble` 在左下角于 hover 或 focus-within 时显示 `Copy`，复制源 `entry.contentText` 原始字符串（可能包含 markdown 标记与代码块），而非渲染后的 DOM 纯文本。`final_answer` 的 `AgentMessageContent` 已实现“代码块保留现有样式，非代码块交给 `react-markdown + remark-gfm` 渲染，且默认不启用 `rehype-raw`”；`planning/action/system_notice` 走专门时间线卡片样式，其中 `planning` 仍维持“纯文本 + fenced code block”渲染。
7. **ActionStepCard 展示策略更新**: 组件展示顺序为 `Thought -> toolCalls -> Code action -> Error -> Observations -> Footnote -> StepRunningIndicator`；`Action output` 已移除。`Thought` 改为可折叠且默认折叠，仅在存在可展示文本时渲染；提取遵循“结构化优先（JSON.thought）-> 文本回退（从 model_output 的 Thought: 段提取）”，提取失败或为空则不渲染。`toolCalls` 展示区默认折叠，且会过滤 `python_interpreter`，若过滤后为空则该组件不渲染。`Code action` 无内容不渲染，`Error` 仅在 `payload.error` 存在时渲染且默认折叠，`Observations` 仅在有内容时渲染并保留折叠交互。`StepRunningIndicator` 仅在“当前轮（最后一条 user_input 之后）的最新 Action 且会话处于生成中”时显示。此处“不显示”表示组件不渲染，而不是仅折叠内容。
8. **MainScrollArea 空档期运行提示**: 当会话处于生成中，但当前轮（最后一条 `user_input` 之后）尚未出现任何 `planning/action` StepCard，也还没有 live assistant draft 时，在 `TimelineEntryList` 底部展示 `PreStepRunLoading`（轻量 assistant loading 气泡 + `ThinkingDots`）；首个 StepCard、live tool call、或 live assistant draft 出现后自动隐藏。多轮会话中，上一轮 StepCard 不参与当前轮的 loading/running 判定。

### TimelineEntryList 行为接口与受控状态（UI Tree 约定）
1. **行为回调**:
   - `onCopyTimelineEntry(entryId, role)`: 统一处理 user/assistant 时间线文本复制。
   - `onStartEditTimelineEntry(entryId)`: 进入 `user_input` 条目编辑态。
   - `onCancelEditTimelineEntry(entryId)`: 取消编辑并回到 view。
   - `onSubmitEditTimelineEntry(entryId, nextContent)`: 提交编辑并触发“从该条目开始重跑后续 assistant 输出”。
2. **TimelineEntryList 可注入回调**:
   - `onCopyTimelineEntry`
   - `onSubmitEditTimelineEntry`
   - `onCancelEditTimelineEntry`
3. **UserMessageBubble 受控能力**:
   - `isEditing`
   - `isSubmittingEdit`
   - `canEdit` (默认 `true`; 预留历史锁定扩展)
4. **AgentMessageBubble 受控能力**:
   - `onCopyMessage(contentText)` (复制原始 `entry.contentText` 字符串)
5. **LiveTurnOverlay 受控能力**:
   - `optimisticEntries[]`
   - `assistantDraft`
   - `planEntries[]`
   - `toolCalls[]`
   - `waitingForPermission`

### final_answer 渲染策略（已实现）
1. **As-Is（当前实现）**:
   - `AgentMessageContent(content: string)` 先按 ```fenced code block``` 分段。
   - 代码块片段使用现有 `<pre><code>` 样式渲染。
   - 非代码块片段按 `react-markdown + remark-gfm` 渲染（支持标题、列表、加粗、链接、GFM 表格）。
   - 默认不启用 raw HTML（不接入 `rehype-raw`），降低 XSS 风险。
   - 外链统一使用安全属性：`target="_blank"` + `rel="noreferrer noopener"`。
2. **依赖补充**:
   - 前端依赖：`react-markdown`、`remark-gfm`。
3. **复制行为约定**:
   - Copy 操作保持不变：复制源 `entry.contentText` 字符串，不复制渲染后的 DOM 文本。
4. **预期渲染样例（非代码块片段）**:
   - `### 小标题` -> 标题渲染。
   - `- 列表项` -> 列表渲染。
   - `**加粗**` -> 强调渲染。
   - `[文档链接](https://example.com)` -> 链接渲染（附带安全属性）。
   - GFM 表格语法 -> 表格渲染。

### 右侧双栏状态归属（规划约定）
1. **页面级状态**:
   - `activeWorkspaceId`: 当前激活 workspace 的 id。
   - `activeWorkspace.realPath`: `FileTreePanel` 根目录来源。
   - `isRightPanelOpen`: 右侧容器总开关。
   - `isFileTabsPanelOpen`: `FileTabsPanel` 开关。
   - `isFileTreePanelOpen`: `FileTreePanel` 开关。
   - `activeFileTreeTab`: `"changes" | "all"`。
2. **Tabs 状态线（按 session 作用域）**:
   - `layout.tabs(sessionKey)`:
     - `all: string[]`
     - `active?: string`
   - 通过 `tabs.open / setActive / move / close` 读写 `store.sessionTabs[sessionKey]`。
3. **文件树状态线（按 workspace 作用域）**:
   - `file.tree.byWorkspaceId[workspaceId]`:
     - `dir[path].children / loaded / loading / expanded`
     - `node[path]` 映射
   - 通过按 workspace 分桶，避免切换目录时树状态串扰。
4. **文件内容状态线（按 workspace + path 作用域）**:
   - `file.byWorkspaceId[workspaceId][path]`:
     - `content / loaded / loading / error`

### FileTreePanel 工作目录约束（新增）
1. **根锚点约束**:
   - `FileTreePanel` 必须加载 `activeWorkspace` 对应目录，即：
     - `FileTreePanel(workspaceRoot = activeWorkspace.realPath)`
   - 不能以 `sessionKey` 或固定路径作为树根来源。
2. **初始化约束**:
   - `on activeWorkspace change -> reset or switch to tree cache of workspace`。
   - `FileTree(path="")` 表示“相对 `workspaceRoot` 的根路径空串”。
3. **过滤作用域约束**:
   - `changes/all` 的 `diffFiles` 必须限定在当前 `activeWorkspace`，不可跨 workspace 混用。

### Composer `@` 文件搜索状态归属（新增）
1. **作用域约束**:
   - 文件搜索根目录固定为 `activeWorkspacePath`（即当前 `activeWorkspace.realPath`）。
   - workspace 切换后，mention 搜索结果与相对路径计算均切换到新 `activeWorkspacePath`。
2. **状态模型**:
   - `composer.mention.isOpen`
   - `composer.mention.query` (`@` 后的检索词)
   - `composer.mention.anchorRange`
   - `composer.mention.highlightedIndex`
   - `composer.mention.results[]`:
     - `fileName`
     - `absPath` (仅内部使用)
     - `relativePath` (相对 `activeWorkspacePath`)
     - `relativeDir` (相对目录，若父目录即根目录则为空)
   - `composer.mention.selectedFiles[]`:
     - `fileName`
     - `relativePath` (用于 markdown 生成)
     - `absPath` (可选，供运行时解析)
3. **候选目录展示规则**:
   - `FileName` 永远显示。
   - 若父目录等于 `activeWorkspacePath`，不显示 `ParentPathLabel`。
   - 其他目录显示相对 `activeWorkspacePath` 的 `relativeDir`（如 `frontend/components`）。
4. **token 序列化规则**:
   - 统一序列化为 `[file_name](file_relative_path_to_activeWorkspacePath)`。
   - 示例：`[UI-tree.md](frontend/UI-tree.md)`。

### 关键交互流（目标行为）
```text
[UI 事件]
用户点击 FileTree 里的文件节点
  └─ SessionSidePanel.onFileClick(node)
     └─ openTab(file.tab(node.path))
        └─ createOpenSessionFileTab(value)
           ├─ tabs.open(nextTab)
           ├─ path = file.pathFromTab(nextTab)
           ├─ file.load(path@activeWorkspace)
           ├─ openFileTabsPanel()
           └─ tabs.setActive(nextTab)

────────────────────────────────────────────────────────────────

[activeWorkspace 切换]
activeWorkspace 发生变化
  ├─ FileTreePanel 切换数据源到 activeWorkspace.realPath
  ├─ 切换到 file.tree.byWorkspaceId[activeWorkspaceId] 对应缓存
  └─ 保持 UI 开关状态不变(isFileTabsPanelOpen / isFileTreePanelOpen)

────────────────────────────────────────────────────────────────

[文件树加载]
FileTree(path="") 初次渲染
  └─ file.tree.list("")
     └─ listDir(path) 基于 activeWorkspace.realPath 解析相对路径

目录展开
  └─ file.tree.expand(dir)
     ├─ dir.expanded = true
     └─ listDir(dir) 懒加载子节点

All files / Changes 差异:
  - All files: FileTree(path="", modified=diffFiles@activeWorkspace)
  - Changes:   FileTree(path="", allowed=diffFiles@activeWorkspace)
```

```text
[Prompt turn: optimistic + streaming]
用户点击 Send / 按 Enter
   ├─ ComposerInput 立即清空
   ├─ TimelineEntryList 追加 OptimisticUserMessageRow
   ├─ session/prompt 或 session/edit_and_prompt 发出
   ├─ ACP `session/update(plan)` -> LivePlanningStepCard*
   ├─ ACP `session/update(tool_call)` -> LiveToolCallsCard
   ├─ ACP `session/update(agent_message_chunk)` -> LiveAssistantDraftRow 逐步增长
   └─ turn 完成后刷新真实 timeline，并用 PersistedTimelineEntries 替换 LiveTurnOverlay

────────────────────────────────────────────────────────────────

[Permission: Ask first]
PermissionSelector = "Ask first"
   ├─ runtime 命中受控动作(write / edit / python_exec_agent)
   ├─ sidecar 发出 `session/request_permission`
   ├─ Home.pendingPermissionRequest = request
   ├─ ComposerPermissionOverlay 覆盖整个 ComposerInputShell
   ├─ LiveToolCallsCard 显示 "Waiting for permission"
   ├─ Deny -> 返回 `{ outcome: selected, optionId: reject-once }`
   ├─ Allow always -> 切换 PermissionSelector 到 `Auto-allow`，并返回 `{ outcome: selected, optionId: allow-once }`
   ├─ Allow once -> 返回 `{ outcome: selected, optionId: allow-once }`
   └─ Cancel run -> 发送 `session/cancel`，并让 pending permission 以 `cancelled` 收敛
```

```text
[Composer mention: 触发与搜索]
用户在 MentionAwareInputField 输入 "@" 或 "@query"
  ├─ composer.mention.isOpen = true
  ├─ composer.mention.query = query
  └─ 基于 activeWorkspacePath 执行文件名搜索 -> composer.mention.results[]

[Composer mention: 候选渲染]
渲染 MentionResultItem
  ├─ FileName 永远显示
  ├─ relativeDir 为空 -> 不显示 ParentPathLabel
  └─ relativeDir 非空 -> 显示 ParentPathLabel(relativeDir)

[Composer mention: 键盘选择]
用户按 ↑/↓
  └─ composer.mention.highlightedIndex 更新
用户按 Tab/Enter (popover open 且存在高亮项)
  ├─ 选中候选文件
  ├─ 插入 FileMentionToken
  ├─ 序列化为 [file_name](relativePath)
  └─ 关闭 popover, 光标回到 token 后
```

### Composer `@` 文件搜索测试用例（新增）
1. 输入 `@` 时弹出文件候选，输入 `@UI` 时按文件名过滤结果。
2. 候选项中文件位于 workspace 根目录时，仅显示 `FileName`，不显示目录标签。
3. 候选项中文件位于子目录时，显示相对目录 `relativeDir`，不显示绝对路径。
4. 使用上/下键切换高亮项，按 `Tab/Enter` 选中高亮文件并渲染 token。
5. 选中后序列化文本为 `[name](relative/path)`，不出现 `file_abs_path`。
6. 切换 `activeWorkspacePath` 后，候选结果与相对路径计算均基于新 workspace。
