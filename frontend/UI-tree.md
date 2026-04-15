# UI Tree (ConversationStream)

本文档描述当前 `frontend/src` 中会话页的实际 UI 结构与数据映射。当前实现以 `Session.timelineEvents` 作为持久历史源，以 `liveTurnBySession` 和 `pendingPermissionRequest` 作为运行态补充，再通过 `normalizeConversationMessages(...)` 组装最终消息流。

## 1. 页面级组件树

```text
Home (app/page.tsx)
├── Sidebar
│   ├── TopBar
│   │   └── NewSessionButton
│   ├── SessionsSection
│   │   ├── SectionHeader
│   │   │   ├── Label: Sessions
│   │   │   └── AddDirectoryButton
│   │   ├── EmptyProjectsHint
│   │   └── ProjectGroup[]
│   │       ├── ProjectRow
│   │       │   ├── ExpandCollapseButton
│   │       │   ├── ProjectNameButton
│   │       │   └── HoverActions
│   │       │       ├── RenameWorkspaceButton
│   │       │       ├── StartNewSessionButton
│   │       │       └── RemoveWorkspaceButton
│   │       ├── SessionItem[]
│   │       │   ├── SessionSelectButton
│   │       │   ├── RelativeTimeLabel
│   │       │   └── RemoveSessionButton (hover)
│   │       └── ShowMoreSessionsButton (when sessions > SESSION_LIMIT)
│   ├── Footer
│   │   └── SettingsButton
│   ├── ResizeHandle
│   ├── AddDirectoryDialog
│   │   ├── DialogHeader
│   │   ├── NativeDirectoryPickerFlow
│   │   │   ├── SelectDirectoryButton
│   │   │   └── SelectedDirectoryPreview
│   │   ├── ManualDirectoryEntryFlow
│   │   │   ├── DirectoryNameInput
│   │   │   └── DirectoryPathInput
│   │   ├── ValidationError
│   │   └── DialogFooter
│   │       ├── CancelButton
│   │       └── AddDirectoryButton
│   ├── EditWorkspaceDialog
│   │   ├── DirectoryNameInput
│   │   ├── PathLabelInput
│   │   ├── ValidationError
│   │   └── DialogFooter
│   │       ├── CancelButton
│   │       └── SaveChangesButton
│   ├── RemoveWorkspaceAlertDialog
│   │   ├── AlertHeader
│   │   ├── ValidationError
│   │   └── AlertFooter
│   │       ├── CancelButton
│   │       └── RemoveWorkspaceButton
│   └── RemoveSessionAlertDialog
│       ├── AlertHeader
│       ├── ValidationError
│       └── AlertFooter
│           ├── CancelButton
│           └── RemoveSessionButton
├── MainContent
│   ├── Header
│   │   └── ActiveSessionTitle
│   ├── ConversationStream
│   │   ├── EmptyStateHero
│   │   │   ├── CodexLogo
│   │   │   └── Project / Empty copy
│   │   ├── MessageColumn
│   │   │   ├── LoadEarlierButton (when hasMoreHistory)
│   │   │   ├── MessageList
│   │   │   │   ├── timeline_entry -> ConversationEventView
│   │   │   │   │   ├── planning -> PlanningStepCard
│   │   │   │   │   ├── action -> ActionStepCard
│   │   │   │   │   │   ├── StepLabel
│   │   │   │   │   │   ├── ThoughtPanel
│   │   │   │   │   │   ├── ToolCallsPanel
│   │   │   │   │   │   ├── CodeActionPanel
│   │   │   │   │   │   ├── ErrorPanel
│   │   │   │   │   │   ├── ObservationsPanel
│   │   │   │   │   │   ├── StepFootnote
│   │   │   │   │   │   └── RunningIndicator
│   │   │   │   │   ├── user_input -> UserBubble
│   │   │   │   │   │   ├── CopyButton
│   │   │   │   │   │   └── EditButton
│   │   │   │   │   ├── editing user_input -> UserEditComposer
│   │   │   │   │   │   ├── Textarea
│   │   │   │   │   │   ├── CancelButton
│   │   │   │   │   │   └── SendButton
│   │   │   │   │   ├── final_answer -> FinalAnswerContent
│   │   │   │   │   └── thinking / system_notice -> AgentMessageContent
│   │   │   │   ├── live_plan -> LivePlanningStepCard
│   │   │   │   ├── acp_tool_call -> MessageAcpToolCall
│   │   │   │   │   ├── Header(title/kind/status)
│   │   │   │   │   ├── WaitingForPermissionHint
│   │   │   │   │   ├── RawInputBlock
│   │   │   │   │   ├── ContentBlock[]
│   │   │   │   │   │   ├── MarkdownContent
│   │   │   │   │   │   └── DiffBlock
│   │   │   │   │   └── ToolCallIdFootnote
│   │   │   │   ├── available_commands -> MessageAvailableCommands
│   │   │   │   │   ├── SummaryHeader
│   │   │   │   │   └── details
│   │   │   │   │       └── CommandCard[]
│   │   │   │   ├── thinking -> live thinking card
│   │   │   │   ├── assistant_draft -> live assistant draft row
│   │   │   │   └── acp_permission -> MessageAcpPermission
│   │   │   │       ├── PermissionHeader
│   │   │   │       ├── SensitiveActionCard
│   │   │   │       ├── OptionRadioList
│   │   │   │       ├── ActionButtons
│   │   │   │       │   ├── CancelRunButton
│   │   │   │       │   ├── DenyButton
│   │   │   │       │   ├── AllowAlwaysButton
│   │   │   │       │   └── AllowOnceButton
│   │   │   │       └── ResponseSentNotice
│   │   │   ├── PreStepRunLoading (when current turn is running but no step card yet)
│   │   │   └── GeneratingIndicator (when submitting in composer)
│   │   └── ScrollToBottomButton (when detached from bottom)
│   ├── RightSidePanels [design-only, not implemented]
│   │   ├── FileTabsPanel [design-only, not implemented]
│   │   │   ├── Tabs
│   │   │   │   ├── ContextTab
│   │   │   │   └── OpenedFileTabs[]
│   │   │   └── FileTabContent(activeFileTab)
│   │   │       └── FileViewer
│   │   └── FileTreePanel [design-only, not implemented]
│   │       ├── ScopeTabs
│   │       │   ├── ChangesTab
│   │       │   └── AllFilesTab
│   │       ├── ChangesFileTree
│   │       └── FullFileTree
│   └── ComposerPanel
│       ├── HiddenFileInput
│       ├── ComposerCard
│       │   ├── AttachmentChipRow
│       │   │   └── AttachmentChip[]
│       │   │       ├── FileNameTooltip
│       │   │       └── RemoveAttachmentButton
│       │   ├── ComposerTextInput
│       │   ├── MentionAwareInputField [design-only, not implemented]
│       │   │   ├── PlainTextSegment[]
│       │   │   ├── FileMentionToken[]
│       │   │   └── Caret
│       │   ├── FileMentionPopover [design-only, not implemented]
│       │   │   ├── Header: Files
│       │   │   ├── SearchHint
│       │   │   ├── MentionResultList
│       │   │   │   └── MentionResultItem[]
│       │   │   │       ├── FileName
│       │   │   │       └── ParentPathLabel
│       │   │   ├── EmptyState
│       │   │   └── LoadingState
│       │   ├── MentionAssistText [design-only, not implemented]
│       │   │   └── ShortcutHints (↑/↓, Tab, Enter, Esc)
│       │   ├── ProviderNoticeBanner
│       │   │   └── OpenSettingsButton
│       │   ├── RunErrorText
│       │   └── ComposerFooter
│       │       ├── LeftControls
│       │       │   ├── AttachButton
│       │       │   ├── ModelDropdown
│       │       │   │   ├── ProviderGroup[]
│       │       │   │   └── ModelOption[]
│       │       │   ├── ReasoningDropdown
│       │       │   └── PermissionDropdown
│       │       └── RightControls
│       │           ├── MicButton
│       │           ├── StopButton (when generating)
│       │           └── SendButton
└── SettingsModal
     ├── ModalHeader
     │   ├── BackButton (non-root pages only)
     │   ├── Title (depends on currentPage)
     │   └── CloseButton
     └── ModalBody
          ├── page: providers
          │   ├── ConnectedProvidersSection
          │   │   ├── EmptyState
          │   │   └── ConnectedProviderCard[]
          │   │       ├── ProviderSummaryButton
          │   │       │   ├── ProviderIcon
          │   │       │   ├── ProviderName
          │   │       │   ├── ProviderDescription
          │   │       │   ├── HealthBadge
          │   │       │   ├── HealthMessage
          │   │       │   └── ModelCountBadge
          │   │       └── DisconnectProviderButton
          │   ├── PopularProvidersSection
          │   │   └── BuiltinProviderRow[]
          │   │       ├── ProviderIcon
          │   │       ├── ProviderMeta
          │   │       └── ConnectOrEditButton
          │   └── CustomProviderEntry
          │       └── ConfigureCustomProviderButton
          ├── page: custom-provider
          │   ├── IntroCopy
          │   ├── ProviderIdField
          │   ├── DisplayNameField
          │   ├── BaseUrlField
          │   ├── ApiKeyField
          │   ├── ModelsEditor
          │   │   ├── ModelDraftRow[]
          │   │   │   ├── ModelIdInput
          │   │   │   ├── DisplayNameInput
          │   │   │   ├── EnabledCheckbox
          │   │   │   └── RemoveModelButton
          │   │   └── AddModelButton
          │   └── SubmitButton
          ├── page: connect-provider
          │   ├── ProviderIcon
          │   ├── ConnectHeadline
          │   ├── IntroCopy
          │   ├── ApiKeyField
          │   └── SubmitButton
          └── page: provider-config
               ├── ProviderIdField
               ├── DisplayNameField
               ├── BaseUrlField (if not hidden)
               ├── ApiKeyField
               ├── BuiltinModelPolicyEditor (builtin only)
               │   ├── ModelPolicySelect
               │   ├── EnabledModelsChecklist (restricted only)
               │   └── PreferredModelSelect
               ├── ModelsEditor (custom only)
               │   ├── ModelDraftRow[]
               │   └── AddModelButton
               ├── HeadersEditor
               │   ├── HeaderDraftCard[]
               │   │   ├── HeaderNameInput
               │   │   ├── ValueKindSelect
               │   │   ├── PlainOrSecretValueInput
               │   │   └── RemoveHeaderButton
               │   └── AddHeaderButton
               └── SubmitButton
```

说明：

1. `SettingsModal` 不是单一路由页面，而是由 `currentPage` 在一个 modal 内切换 `providers / custom-provider / connect-provider / provider-config` 四种视图。
2. `Sidebar` 内的重命名、删除工作区、删除 session、添加目录都各自挂载对话框或确认弹窗，因此完整 UI tree 需要把这些 overlay 一并记入。
3. `MainContent` 的树除了 `ConversationStream` 外，还包含完整 composer 区域；此前文档主要遗漏的是这一层和 `SettingsModal`/`Sidebar` 的细节。
4. 根据 `git diff` 中保留的旧版设计树，`RightSidePanels`、`FileTabsPanel`、`FileTreePanel`、`MentionAwareInputField`、`FileMentionPopover`、`MentionAssistText` 已作为“设计预留，未实现”补回文档，但它们当前不在 `frontend/src` 的实际渲染路径中。

## 2. 消息组装流程

```text
ACP + local session state
  -> Session.timelineEvents
  -> liveTurnBySession[activeSessionId]
  -> pendingPermissionRequest
  -> mergeTimelineEventsWithLiveTurn(...)
       -> projectConversationEvents(...)
       -> persisted timeline entries + optimistic user entry merge
  -> normalizeConversationMessages(...)
       -> append live_plan / acp_tool_call / available_commands /
          thinking / assistant_draft / acp_permission
  -> ConversationStream
  -> MessageList switch(type)
```

关键点：

1. `timelineEvents` 先通过 `projectConversationEvents(...)` 投影为 `ConversationEvent[]`，再和 `liveTurn.optimisticEvents` 合并。
2. `liveTurn` 中的计划、工具调用、可用命令、思考草稿、助手草稿并不会直接写进 `timeline_entry`，而是作为独立的 live message 追加到消息尾部。
3. `pendingPermissionRequest` 无论是否存在 `liveTurn`，都会在 `normalizeConversationMessages(...)` 的最后追加一条 `acp_permission`。

## 3. `timeline_entry` 内部映射

`timeline_entry` 对应的是 `ConversationEventView`，不是单独的 ACP 卡片。当前 `projectConversationEvents(...)` 会把 timeline 投影成以下事件种类：

```text
ConversationEvent.entryKind                Render path
---------------------------------------------------------------
user_input                                right-aligned user bubble
planning                                  PlanningStepCard
action                                    ActionStepCard
final_answer                              FinalAnswerContent (Markdown + code blocks)
thinking                                  AgentMessageContent
system_notice                             AgentMessageContent
```

其中：

1. `tool_call` / `tool_call_update` 在持久化历史中会被投影为 `action`，并在 `ActionStepCard` 中展示工具调用、observation、code action、error、usage、duration 等面板。
2. `plan` 在持久化历史中会被投影为 `planning`；运行中则另外显示为 `live_plan`。
3. `agent_thought_chunk` 在持久化历史中会被投影为 `thinking`；运行中同时还会显示 live `thinking` 卡片。

## 4. ACP / Live 更新到 UI 的映射

```text
Source                                      Stored in state                     Rendered as                     Persistence
--------------------------------------------------------------------------------------------------------------------------
timeline/list, session/load                 Session.timelineEvents             timeline_entry                  persisted
local send / edit-and-rerun                 liveTurn.optimisticEvents          timeline_entry                  live-only until hydration
session/update(agent_message_chunk)         liveTurn.assistantDraft            assistant_draft                 live-only
session/update(agent_thought_chunk)         liveTurn.thinkingDraft             thinking                        live-only before hydration
session/update(plan)                        liveTurn.planEntries               live_plan                       live-only before hydration
session/update(tool_call)                   liveTurn.toolCalls                 acp_tool_call                   live-only before hydration
session/update(tool_call_update)            liveTurn.toolCalls                 acp_tool_call (updated)         live-only before hydration
session/update(available_commands_update)   liveTurn.availableCommands         available_commands              live-only
session/request_permission                  pendingPermissionRequest           acp_permission                  live-only
```

补充说明：

1. run 完成后，`hydrateSessionAfterRun(...)` 会重新拉取 timeline，并清空该 session 的 `liveTurn`。
2. 因此很多运行态卡片会在 hydration 后“落盘”为 `timeline_entry`，但 `available_commands` 和 `acp_permission` 仍然只存在于 live UI。
3. 当前前端还会在没有任何步骤卡片但 session 已进入运行态时显示 `PreStepRunLoading`，这不是 ACP message type，而是 `ConversationStream` 的额外过渡 UI。

## 5. 当前实现约束

1. UI 状态仍然是 `session/turn` 语义，未引入 `activeRunId` 或独立 run timeline。
2. 历史加载是分页式的，`ConversationStream` 顶部通过 `Load earlier messages` 触发更早 timeline 拉取，并保持滚动位置。
3. 用户消息支持在 `ConversationEventView` 中直接编辑并触发 `session/edit_and_prompt` 重跑。
4. `SettingsModal` 只负责 provider 配置；会话页主体的模型、reasoning、permission 选择都位于 `MainContent` 的 composer 区域。
