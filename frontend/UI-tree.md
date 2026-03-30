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
│   │           └── ToggleMoreButton("Show more" / "Show less", 基于内部 isExpanded 状态切换)
│   ├── FooterNav
│   │   └── NavItem("Settings", icon=Settings)
│   └── ResizeHandle (右侧边界拖拽条, cursor-col-resize)
│
├── MainContent (flex-col, h-screen)
│   ├── Header (顶部固定)
│   │   └── Title({activeSessionLabel})
│   ├── MainScrollArea (主内容滚动区 / flex-1, overflow-y-auto, relative 定位)
│   │   ├── (条件渲染: 无对话记录时)
│   │   │   └── EmptyStateHero
│   │   │       ├── CodexLogo (SVG)
│   │   │       ├── Heading("Let's start")
│   │   │       └── SubHeading({activeProjectLabel})
│   │   ├── (条件渲染: 有对话记录时)
│   │   │   └── TimelineEntryList
│   │   │       ├── TimelineEntryRow(entryKind="user_input", role="user")
│   │   │       │   ├── UserMessageBubble ("User message")
│   │   │       │   │   ├── 状态: view
│   │   │       │   │   │   └── BubbleContent (whitespace-pre-wrap)
│   │   │       │   │   ├── 状态: editing (条件渲染: isEditing)
│   │   │       │   │   │   └── InlineEditPanel
│   │   │       │   │   │       ├── EditTextarea(initial=user content)
│   │   │       │   │   │       └── EditActions
│   │   │       │   │   │           ├── Button("Cancel")
│   │   │       │   │   │           └── Button("Send")
│   │   │       │   │   └── 状态: submitting (条件渲染: isSubmittingEdit; Send disabled/loading)
│   │   │       │   └── BubbleActions (anchor=right-bottom, hover 或 focus-within 时显示)
│   │   │       │       ├── ActionButton("Copy", icon=Copy)
│   │   │       │       ├── ActionButton("Edit", icon=Pencil)
│   │   │       │       └── Tooltip("Copy" / "Edit", hover 或 focus 时显示)
│   │   │       ├── TimelineEntryRow(entryKind="planning", role="assistant")
│   │   │       │   └── PlanningStepCard
│   │   │       ├── TimelineEntryRow(entryKind="action", role="assistant")
│   │   │       │   └── ActionStepCard
│   │   │       │       ├── ThoughtPanel (可折叠，默认折叠；仅存在可展示 Thought 文本时渲染)
│   │   │       │       │   └── 提取策略: 结构化优先（JSON.thought）-> 文本回退（从 model_output 的 Thought: 段提取）
│   │   │       │       ├── ToolCallsPanel (默认折叠; 仅过滤后存在可展示 toolCalls 时渲染)
│   │   │       │       │   └── 过滤规则: name="python_interpreter" 的 tool call 不展示
│   │   │       │       ├── CodeActionPanel (仅有内容时渲染)
│   │   │       │       ├── ObservationsPanel (仅有内容时渲染; 保留 Show/Hide 折叠交互)
│   │   │       │       ├── StepFootnote
│   │   │       │       └── StepRunningIndicator (仅当前 activeRunId 下最新 Action 且会话处于 running/queued 时显示)
│   │   │       ├── TimelineEntryRow(entryKind="final_answer", role="assistant")
│   │   │           ├── AgentAvatar (gradient dot)
│   │   │           └── AgentMessageBubble
│   │   │               ├── AgentMessageContent (final_answer 已实现: fenced code block 分段 + 非代码块 Markdown 渲染)
│   │   │               │   ├── As-Is: CodeBlock (``` fenced block -> <pre><code> 样式)
│   │   │               │   ├── As-Is: MarkdownPart (非代码块片段 -> react-markdown + remark-gfm)
│   │   │               │   ├── As-Is: 代码块片段继续沿用现有 <pre><code> 视觉样式
│   │   │               │   ├── As-Is: 默认不启用 raw HTML (不接入 rehype-raw)
│   │   │               │   └── As-Is: 外链使用 target="_blank" + rel="noreferrer noopener"
│   │   │               └── MessageActions (anchor=left-bottom, hover 或 focus-within 时显示)
│   │   │                   ├── ActionButton("Copy", icon=Copy)
│   │   │                   └── Tooltip("Copy", hover 或 focus 时显示)
│   │   │       ├── PreStepRunLoading (仅会话 running/queued 且当前 activeRunId 尚无 planning/action StepCard 时显示)
│   │   │       │   └── 隐藏条件: 首个 StepCard 出现或 run 结束
│   │   │       └── TimelineEntryRow(entryKind="system_notice", role="system")
│   │   │           └── System notice bubble
│   │   └── ScrollToBottomButton (绝对定位 absolute bottom, icon=ArrowDown, 未到底部时显示)
│   └── ComposerPanel (底部固定)
│       ├── InputField(placeholder="Ask for follow-up changes")
│       └── Toolbar
│           ├── LeftActions
│           │   ├── IconButton(icon=Plus, 支持从 activeProject 所在文件夹中选取文件)
│           │   ├── ModelSelector({selectedModel}, icon=ChevronDown)
│           │   │   └── DropdownMenu(List of Models)
│           │   ├── ReasoningSelector
│           │   │   └── DropdownMenu(Auto / low / medium / high ...)
│           │   └── PermissionSelector(icon=ShieldCheck)
│           │       └── DropdownMenu("Default permissions", "Full access")
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

### 分析总结
1. **外层布局**: 使用 `Home` 组件作为主视图。采用左右分栏的弹性布局（`flex h-screen`），左侧是**宽度可调**的 `Sidebar`（借助 ResizeHandle 拖拽更新宽度状态），右侧自适应撑满的是 `MainContent`，在需要时作为 Overlay 弹出 `SettingsModal`。
2. **状态驱动的设置面板**: `SettingsModal` 没有采用跳转页面的方式，而是通过内部维护一个 `currentPage` 状态来实现不同配置项步骤（提供商列表、自定义提供商、连接提供商、已连接提供商编辑）间的切换。已连接 provider 列表已提升到 `Home` 页面级 state，通过 props 注入 `SettingsModal`，不再由 modal 内部独立持有。
3. **复合交互的侧边栏**: 项目组通过 `hover` 状态挂载了比较多的隐藏交互（例如针对 "github" Project 的 "More options" 菜单和 "Start new session" 悬浮按钮）。**强调：类似 DropdownMenu 这样的弹出菜单需采用 Portal 等机制渲染脱离普通文档流，严格避免被 Sidebar 的边界或 `overflow` 属性遮挡。**
4. **主内容区的滚动与状态流**: `MainContent` 内部采用纵向弹性（`flex-col`），首尾的 `Header` 和 `ComposerPanel` 空间固定，中间使用 `flex-1 overflow-y-auto` 划分出独立的一块主内容滚动区域。并利用这块滚动容器的相对定位能力，放置了一个通过滚动距离判定的绝对位置悬浮“到底部”快捷按钮。`ModelSelector` 的候选模型仅来自**已完成校验且状态为 `connected`** 的 provider；`Not validated` 或校验失败的 provider 不会进入模型列表。
5. **Provider 管理流已闭环**: `ProvidersPage` 不再只是空状态展示，当前已支持“查看 Connected providers -> 点击进入 ProviderConfigPage 编辑 -> 断开删除”的完整管理流；`PopularProviders` 对已连接项会切换为 `Edit` 而非重复 `+ Connect`。在保存 provider 后，前端会自动对其**主模型（优先 `preferredModelId`）**发起一次真实连通性校验，校验结果会回写到 `ConnectedProviders` 的状态 badge / message，并进一步决定其模型是否可以出现在 `ModelSelector`。
6. **时间线条目动作与编辑态**: `TimelineEntryList` 采用按 `entryKind + displayRole` 的渲染分支。`UserMessageBubble` 在右下角于 hover 或 focus-within 时显示 `Copy` 与 `Edit`，点击 `Edit` 进入行内编辑态（`EditTextarea + Cancel/Send`），提交中进入 `submitting` 子状态。`AgentMessageBubble` 在左下角于 hover 或 focus-within 时显示 `Copy`，复制源 `entry.contentText` 原始字符串（可能包含 markdown 标记与代码块），而非渲染后的 DOM 纯文本。`final_answer` 的 `AgentMessageContent` 已实现“代码块保留现有样式，非代码块交给 `react-markdown + remark-gfm` 渲染，且默认不启用 `rehype-raw`”；`planning/action/system_notice` 走专门时间线卡片样式，其中 `planning` 仍维持“纯文本 + fenced code block”渲染。
7. **ActionStepCard 展示策略更新**: 组件展示顺序为 `Thought -> toolCalls -> Code action -> Observations -> Footnote -> StepRunningIndicator`；`Action output` 已移除。`Thought` 改为可折叠且默认折叠，仅在存在可展示文本时渲染；提取遵循“结构化优先（JSON.thought）-> 文本回退（从 model_output 的 Thought: 段提取）”，提取失败或为空则不渲染。`toolCalls` 展示区默认折叠，且会过滤 `python_interpreter`，若过滤后为空则该组件不渲染。`Code action` 无内容不渲染，`Observations` 仅在有内容时渲染并保留折叠交互。`StepRunningIndicator` 仅在“当前 `activeRunId` 下的最新 Action 且会话处于 running/queued”时显示。此处“不显示”表示组件不渲染，而不是仅折叠内容。
8. **MainScrollArea 空档期运行提示**: 当会话已进入 `running/queued`，但时间线里当前 `activeRunId` 仍未出现任何 `planning/action` StepCard 时，在 `TimelineEntryList` 底部展示 `PreStepRunLoading`（轻量 assistant loading 气泡 + `ThinkingDots`）；首个 StepCard 出现或 run 结束后自动隐藏。多轮会话中，上一轮 StepCard 不参与当前 run 的 loading/running 判定。

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
