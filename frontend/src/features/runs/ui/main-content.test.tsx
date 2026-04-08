import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

import { MainContent } from "@/features/runs"
import type { TimelineEntry } from "@/shared/api/sidecar"
import type { ModelOptionGroup, SelectedModelRef } from "@/features/providers"

const groups: ModelOptionGroup[] = [
  {
    connectionId: "prov_openai",
    providerLabel: "OpenAI",
    providerId: "openai",
    models: [
      {
        connectionId: "prov_openai",
        providerLabel: "OpenAI",
        providerId: "openai",
        modelId: "gpt-5.4",
        label: "gpt-5.4",
        supportsReasoningTokens: true,
        reasoningEffortValues: ["none", "low", "medium", "high"],
      },
    ],
  },
  {
    connectionId: "prov_gemini",
    providerLabel: "Gemini",
    providerId: "gemini",
    models: [
      {
        connectionId: "prov_gemini",
        providerLabel: "Gemini",
        providerId: "gemini",
        modelId: "gemini/gemini-2.5-pro",
        label: "gemini/gemini-2.5-pro",
        supportsReasoningTokens: true,
      },
    ],
  },
]

function buildSession(timelineEntries: TimelineEntry[] = []) {
  return {
    id: "sess_1",
    workspaceId: "ws_1",
    title: "New session",
    titleSource: "placeholder" as const,
    createdAt: "2026-03-24T12:00:00Z",
    updatedAt: "2026-03-24T12:00:00Z",
    lastMessageAt: null,
    messageCount: timelineEntries.length,
    lastMessagePreview: null,
    activeConnectionId: null,
    activeModelId: null,
    timelineEntries,
    hasMoreHistory: false,
    nextBeforeSequence: null,
    isLoadingHistory: false,
    timelineHydrationStatus: "loaded" as const,
  }
}

function renderMainContent(
  selection: SelectedModelRef | null,
  timelineEntries: TimelineEntry[] = [],
  options?: { isSessionRunning?: boolean; activeRunId?: string | null }
) {
  const onSelectedModelChange = vi.fn()

  render(
    <MainContent
      activeProject={{ id: "ws_1", name: "nutstore-bot", path: "/tmp/nutstore-bot", sessions: [] }}
      activeSession={buildSession(timelineEntries)}
      isDraftSession={false}
      onSendMessage={vi.fn()}
      modelOptionGroups={groups}
      selectedModel={selection}
      selectedReasoningEffort={null}
      onSelectedModelChange={onSelectedModelChange}
      onSelectedReasoningEffortChange={vi.fn()}
      isLoadingModels={false}
      providerError={null}
      runError={null}
      hasMoreHistory={false}
      isLoadingHistory={false}
      onLoadEarlierTimeline={vi.fn(async () => undefined)}
      composerAttachments={[]}
      isUploadingAttachment={false}
      onAttachFiles={vi.fn(async () => undefined)}
      onRemoveAttachment={vi.fn(async () => undefined)}
      onEditTimelineEntryAndRerun={vi.fn(async () => undefined)}
      isSessionRunning={options?.isSessionRunning ?? false}
      activeRunId={options?.activeRunId ?? null}
    />
  )

  return { onSelectedModelChange }
}

const flushMicrotasks = async () => {
  await Promise.resolve()
  await Promise.resolve()
}

afterEach(() => {
  vi.useRealTimers()
})

describe("MainContent model selector", () => {
  it("renders grouped provider label for the selected model", () => {
    renderMainContent({ connectionId: "prov_openai", modelId: "gpt-5.4" })

    expect(screen.getByText("OpenAI - gpt-5.4")).toBeInTheDocument()
  })

  it("shows grouped options and emits connection-aware selections", () => {
    const { onSelectedModelChange } = renderMainContent({
      connectionId: "prov_openai",
      modelId: "gpt-5.4",
    })

    fireEvent.click(screen.getByText("OpenAI - gpt-5.4"))
    expect(screen.getByText("OpenAI")).toBeInTheDocument()
    expect(screen.getByText("Gemini")).toBeInTheDocument()

    fireEvent.click(screen.getByText("gemini/gemini-2.5-pro"))

    expect(onSelectedModelChange).toHaveBeenCalledWith({
      connectionId: "prov_gemini",
      modelId: "gemini/gemini-2.5-pro",
    })
  })

  it("disables model selection and submit when no configured providers exist", () => {
    render(
      <MainContent
        activeProject={{ id: "ws_1", name: "nutstore-bot", path: "/tmp/nutstore-bot", sessions: [] }}
        activeSession={buildSession()}
        isDraftSession={false}
        onSendMessage={vi.fn()}
        modelOptionGroups={[]}
        selectedModel={null}
        selectedReasoningEffort={null}
        onSelectedModelChange={vi.fn()}
        onSelectedReasoningEffortChange={vi.fn()}
        isLoadingModels={false}
        providerError={null}
        runError={null}
        hasMoreHistory={false}
        isLoadingHistory={false}
        onLoadEarlierTimeline={vi.fn(async () => undefined)}
        composerAttachments={[]}
        isUploadingAttachment={false}
        onAttachFiles={vi.fn(async () => undefined)}
        onRemoveAttachment={vi.fn(async () => undefined)}
        onEditTimelineEntryAndRerun={vi.fn(async () => undefined)}
      />
    )

    expect(screen.getByText("No configured providers").closest("button")).toBeDisabled()
    expect(screen.getByLabelText("Send")).toBeDisabled()
  })

  it("offers settings shortcut when providers are not configured", () => {
    const onOpenSettings = vi.fn()

    render(
      <MainContent
        activeProject={{ id: "ws_1", name: "nutstore-bot", path: "/tmp/nutstore-bot", sessions: [] }}
        activeSession={buildSession()}
        isDraftSession={false}
        onSendMessage={vi.fn()}
        modelOptionGroups={[]}
        selectedModel={null}
        selectedReasoningEffort={null}
        onSelectedModelChange={vi.fn()}
        onSelectedReasoningEffortChange={vi.fn()}
        isLoadingModels={false}
        providerError={null}
        runError={null}
        hasMoreHistory={false}
        isLoadingHistory={false}
        onLoadEarlierTimeline={vi.fn(async () => undefined)}
        composerAttachments={[]}
        isUploadingAttachment={false}
        onAttachFiles={vi.fn(async () => undefined)}
        onRemoveAttachment={vi.fn(async () => undefined)}
        onEditTimelineEntryAndRerun={vi.fn(async () => undefined)}
        onOpenSettings={onOpenSettings}
      />
    )

    fireEvent.click(screen.getByRole("button", { name: "Open Settings" }))
    fireEvent.click(screen.getByText("No configured providers"))

    expect(onOpenSettings).toHaveBeenCalledTimes(2)
  })

  it("shows reasoning effort selector when the selected model supports it", () => {
    renderMainContent({ connectionId: "prov_openai", modelId: "gpt-5.4" })

    expect(screen.getByRole("button", { name: "Reasoning effort" })).toBeInTheDocument()
    expect(screen.getByText("Auto")).toBeInTheDocument()
  })

  it("renders persisted planning and action entries with action display priority", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_user",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "Inspect the workspace",
          createdAt: "2026-03-24T12:00:00Z",
        },
        {
          id: "entry_plan",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 2,
          entryKind: "planning",
          displayRole: "assistant",
          stepId: "step-1",
          stepNumber: null,
          contentText: "Inspect the repo and identify the main entry points.",
          createdAt: "2026-03-24T12:00:01Z",
        },
        {
          id: "entry_action",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 3,
          entryKind: "action",
          displayRole: "assistant",
          stepId: "step-2",
          stepNumber: 1,
          contentText: null,
          createdAt: "2026-03-24T12:00:02Z",
          contentJson: {
            thought: "Thought: I will inspect output first.",
            toolCalls: [
              { id: "call_1", name: "python_interpreter", argumentsText: 'print("hidden")' },
              { id: "call_2", name: "grep", argumentsText: 'pattern="hello"' },
            ],
            observations: ["Execution logs:", "hello"],
            codeAction: 'print("hello")',
            actionOutput: { result: "hello" },
            error: "Minor warning",
            usage: { inputTokens: 12, outputTokens: 4, reasoningTokens: 0 },
            durationMs: 180,
          },
        },
      ]
    )

    expect(screen.getByText("Planning step")).toBeInTheDocument()
    expect(screen.getByText("Inspect the repo and identify the main entry points.")).toBeInTheDocument()
    expect(screen.getByText("Step 1")).toBeInTheDocument()
    expect(screen.queryByText("I will inspect output first.")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Show Thought" })).toBeInTheDocument()
    expect(screen.queryByText("print(\"hello\")")).not.toBeInTheDocument()
    expect(screen.queryByText('grep(pattern="hello")')).not.toBeInTheDocument()
    expect(screen.queryByText('python_interpreter(print("hidden"))')).not.toBeInTheDocument()
    expect(screen.queryByText((content) => content.includes('"result": "hello"'))).not.toBeInTheDocument()
    expect(screen.queryByText("Execution logs:\nhello")).not.toBeInTheDocument()
    expect(screen.getByText("Minor warning")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Tool calls/i }))
    expect(screen.getByText('grep(pattern="hello")')).toBeInTheDocument()
    expect(screen.queryByText('python_interpreter(print("hidden"))')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Thought/i }))
    expect(screen.getByText("I will inspect output first.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Thought/i }))
    expect(screen.queryByText("I will inspect output first.")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Code action/i }))
    expect(screen.getByText('print("hello")')).toBeInTheDocument()
  })

  it("shows running indicator only for latest action while session is running", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_action_1",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "action",
          displayRole: "assistant",
          stepId: "step-1",
          stepNumber: 1,
          contentText: null,
          createdAt: "2026-03-24T12:00:01Z",
          contentJson: {
            thought: "Thought: first step",
            toolCalls: [],
            observations: ["Execution logs:", "first"],
            codeAction: null,
            actionOutput: null,
            error: null,
            usage: { inputTokens: 12, outputTokens: 4, reasoningTokens: 0 },
            durationMs: 180,
          },
        },
        {
          id: "entry_action_2",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 2,
          entryKind: "action",
          displayRole: "assistant",
          stepId: "step-2",
          stepNumber: 2,
          contentText: null,
          createdAt: "2026-03-24T12:00:02Z",
          contentJson: {
            thought: "Thought: second step",
            toolCalls: [],
            observations: ["Execution logs:", "second"],
            codeAction: null,
            actionOutput: null,
            error: null,
            usage: { inputTokens: 15, outputTokens: 6, reasoningTokens: 0 },
            durationMs: 220,
          },
        },
      ],
      { isSessionRunning: true, activeRunId: "run_1" }
    )

    expect(screen.getByText("Running...")).toBeInTheDocument()
    expect(screen.getAllByText("Step 1")).toHaveLength(1)
    expect(screen.getAllByText("Step 2")).toHaveLength(1)
    expect(screen.getAllByText("Running...")).toHaveLength(1)
  })

  it("hides running indicator when session is not running", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_action_1",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "action",
          displayRole: "assistant",
          stepId: "step-1",
          stepNumber: 1,
          contentText: null,
          createdAt: "2026-03-24T12:00:01Z",
          contentJson: {
            thought: "Thought: first step",
            toolCalls: [],
            observations: ["Execution logs:", "first"],
            codeAction: null,
            actionOutput: null,
            error: null,
            usage: { inputTokens: 12, outputTokens: 4, reasoningTokens: 0 },
            durationMs: 180,
          },
        },
      ],
      { isSessionRunning: false }
    )

    expect(screen.queryByText("Running...")).not.toBeInTheDocument()
  })

  it("scopes running indicator to latest action within active run id", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_action_old_run",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "action",
          displayRole: "assistant",
          stepId: "step-1",
          stepNumber: 1,
          contentText: null,
          createdAt: "2026-03-24T12:00:01Z",
          contentJson: {
            thought: "Thought: old run",
            toolCalls: [],
            observations: ["Execution logs:", "old"],
            codeAction: null,
            actionOutput: null,
            error: null,
            usage: { inputTokens: 10, outputTokens: 3, reasoningTokens: 0 },
            durationMs: 120,
          },
        },
        {
          id: "entry_user_new_run",
          sessionId: "sess_1",
          runId: "run_2",
          sequenceNo: 2,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "new run question",
          createdAt: "2026-03-24T12:00:03Z",
        },
      ],
      { isSessionRunning: true, activeRunId: "run_2" }
    )

    expect(screen.queryByText("Running...")).not.toBeInTheDocument()
    expect(screen.getByTestId("pre-step-run-loading")).toBeInTheDocument()
  })

  it("shows pre-step loading while session is running before first step card", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_user_1",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "Run through sidecar",
          createdAt: "2026-03-24T12:00:00Z",
        },
      ],
      { isSessionRunning: true, activeRunId: "run_1" }
    )

    expect(screen.getByTestId("pre-step-run-loading")).toBeInTheDocument()
  })

  it("hides pre-step loading once first step card appears", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_user_1",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "Run through sidecar",
          createdAt: "2026-03-24T12:00:00Z",
        },
        {
          id: "entry_plan_1",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 2,
          entryKind: "planning",
          displayRole: "assistant",
          stepId: "step-1",
          stepNumber: null,
          contentText: "Inspect the workspace",
          createdAt: "2026-03-24T12:00:01Z",
        },
      ],
      { isSessionRunning: true, activeRunId: "run_1" }
    )

    expect(screen.queryByTestId("pre-step-run-loading")).not.toBeInTheDocument()
  })

  it("does not show pre-step loading while composer is generating", async () => {
    let resolveSend: (() => void) | null = null
    const onSendMessage = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSend = resolve
        })
    )

    render(
      <MainContent
        activeProject={{ id: "ws_1", name: "nutstore-bot", path: "/tmp/nutstore-bot", sessions: [] }}
        activeSession={buildSession([
          {
            id: "entry_user_1",
            sessionId: "sess_1",
            runId: "run_1",
            sequenceNo: 1,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Run through sidecar",
            createdAt: "2026-03-24T12:00:00Z",
          },
        ])}
        isDraftSession={false}
        onSendMessage={onSendMessage}
        modelOptionGroups={groups}
        selectedModel={{ connectionId: "prov_openai", modelId: "gpt-5.4" }}
        selectedReasoningEffort={null}
        onSelectedModelChange={vi.fn()}
        onSelectedReasoningEffortChange={vi.fn()}
        isLoadingModels={false}
        providerError={null}
        runError={null}
        hasMoreHistory={false}
        isLoadingHistory={false}
        onLoadEarlierTimeline={vi.fn(async () => undefined)}
        composerAttachments={[]}
        isUploadingAttachment={false}
        onAttachFiles={vi.fn(async () => undefined)}
        onRemoveAttachment={vi.fn(async () => undefined)}
        onEditTimelineEntryAndRerun={vi.fn(async () => undefined)}
        isSessionRunning={true}
        activeRunId="run_2"
      />
    )

    fireEvent.change(screen.getByPlaceholderText("Ask for follow-up changes"), {
      target: { value: "go" },
    })
    fireEvent.click(screen.getByLabelText("Send"))

    await waitFor(() => {
      expect(onSendMessage).toHaveBeenCalledWith("go")
    })
    expect(screen.queryByTestId("pre-step-run-loading")).not.toBeInTheDocument()

    await act(async () => {
      resolveSend?.()
      await flushMicrotasks()
    })
  })

  it("renders timeline entry actions and supports inline edit submit", async () => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(async () => undefined),
      },
    })
    const onEditTimelineEntryAndRerun = vi.fn(async () => undefined)

    render(
      <MainContent
        activeProject={{ id: "ws_1", name: "nutstore-bot", path: "/tmp/nutstore-bot", sessions: [] }}
        activeSession={buildSession([
          {
            id: "msg_user",
            sessionId: "sess_1",
            runId: "run_1",
            sequenceNo: 1,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "please update this",
            createdAt: "2026-03-24T12:00:00Z",
          },
          {
            id: "msg_assistant",
            sessionId: "sess_1",
            runId: "run_1",
            sequenceNo: 2,
            entryKind: "final_answer",
            displayRole: "assistant",
            stepId: null,
            stepNumber: null,
            contentText: "```ts\nconsole.log('a')\n```",
            createdAt: "2026-03-24T12:00:01Z",
          },
        ])}
        isDraftSession={false}
        onSendMessage={vi.fn()}
        modelOptionGroups={groups}
        selectedModel={{ connectionId: "prov_openai", modelId: "gpt-5.4" }}
        selectedReasoningEffort={null}
        onSelectedModelChange={vi.fn()}
        onSelectedReasoningEffortChange={vi.fn()}
        isLoadingModels={false}
        providerError={null}
        runError={null}
        hasMoreHistory={false}
        isLoadingHistory={false}
        onLoadEarlierTimeline={vi.fn(async () => undefined)}
        composerAttachments={[]}
        isUploadingAttachment={false}
        onAttachFiles={vi.fn(async () => undefined)}
        onRemoveAttachment={vi.fn(async () => undefined)}
        onEditTimelineEntryAndRerun={onEditTimelineEntryAndRerun}
      />
    )

    fireEvent.click(screen.getByRole("button", { name: "Copy user message" }))
    fireEvent.click(screen.getByRole("button", { name: "Copy assistant message" }))
    expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(1, "please update this")
    expect(navigator.clipboard.writeText).toHaveBeenNthCalledWith(2, "```ts\nconsole.log('a')\n```")

    fireEvent.click(screen.getByRole("button", { name: "Edit user message" }))
    const editor = screen.getByDisplayValue("please update this")
    fireEvent.change(editor, { target: { value: "please update this now" } })
    fireEvent.click(screen.getByText("Send"))

    await waitFor(() => {
      expect(onEditTimelineEntryAndRerun).toHaveBeenCalledWith("msg_user", "please update this now")
    })
  })

  it("shows check icon for 3 seconds after copying a user message", async () => {
    vi.useFakeTimers()
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(async () => undefined),
      },
    })

    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "msg_user",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "please update this",
          createdAt: "2026-03-24T12:00:00Z",
        },
      ]
    )

    const userCopyButton = screen.getByRole("button", { name: "Copy user message" })
    expect(userCopyButton.querySelector("svg.lucide-copy")).toBeTruthy()
    expect(userCopyButton.querySelector("svg.lucide-check")).toBeFalsy()

    await act(async () => {
      fireEvent.click(userCopyButton)
      await flushMicrotasks()
    })
    expect(userCopyButton.querySelector("svg.lucide-check")).toBeTruthy()
    expect(userCopyButton.querySelector("svg.lucide-copy")).toBeFalsy()

    await act(async () => {
      vi.advanceTimersByTime(3000)
      await flushMicrotasks()
    })
    expect(userCopyButton.querySelector("svg.lucide-copy")).toBeTruthy()
    expect(userCopyButton.querySelector("svg.lucide-check")).toBeFalsy()
  })

  it("resets copy success state using the latest click timer", async () => {
    vi.useFakeTimers()
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(async () => undefined),
      },
    })

    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "msg_user",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "please update this",
          createdAt: "2026-03-24T12:00:00Z",
        },
        {
          id: "msg_assistant",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 2,
          entryKind: "final_answer",
          displayRole: "assistant",
          stepId: null,
          stepNumber: null,
          contentText: "done",
          createdAt: "2026-03-24T12:00:01Z",
        },
      ]
    )

    const userCopyButton = screen.getByRole("button", { name: "Copy user message" })
    const assistantCopyButton = screen.getByRole("button", { name: "Copy assistant message" })

    await act(async () => {
      fireEvent.click(userCopyButton)
      await flushMicrotasks()
    })
    expect(userCopyButton.querySelector("svg.lucide-check")).toBeTruthy()

    await act(async () => {
      vi.advanceTimersByTime(1000)
      fireEvent.click(assistantCopyButton)
      await flushMicrotasks()
    })
    expect(userCopyButton.querySelector("svg.lucide-copy")).toBeTruthy()
    expect(userCopyButton.querySelector("svg.lucide-check")).toBeFalsy()
    expect(assistantCopyButton.querySelector("svg.lucide-check")).toBeTruthy()

    await act(async () => {
      vi.advanceTimersByTime(2999)
    })
    expect(assistantCopyButton.querySelector("svg.lucide-check")).toBeTruthy()

    await act(async () => {
      vi.advanceTimersByTime(1)
      await flushMicrotasks()
    })
    expect(assistantCopyButton.querySelector("svg.lucide-copy")).toBeTruthy()
    expect(assistantCopyButton.querySelector("svg.lucide-check")).toBeFalsy()
  })

  it("does not show check icon when clipboard copy fails", async () => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(async () => {
          throw new Error("copy failed")
        }),
      },
    })

    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "msg_assistant",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "final_answer",
          displayRole: "assistant",
          stepId: null,
          stepNumber: null,
          contentText: "done",
          createdAt: "2026-03-24T12:00:01Z",
        },
      ]
    )

    const assistantCopyButton = screen.getByRole("button", { name: "Copy assistant message" })
    fireEvent.click(assistantCopyButton)

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith("done")
      expect(assistantCopyButton.querySelector("svg.lucide-copy")).toBeTruthy()
      expect(assistantCopyButton.querySelector("svg.lucide-check")).toBeFalsy()
    })
  })

  it("renders markdown for final_answer while keeping copy payload as raw source", async () => {
    const rawMarkdown = [
      "### 请假流程",
      "",
      "- **请假前**：先与直属Leader沟通",
      "",
      "[查看文档](https://example.com/policy)",
      "",
      "| 字段 | 说明 |",
      "| --- | --- |",
      "| A | B |",
      "",
      "```ts",
      "console.log('hello')",
      "```",
    ].join("\n")

    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(async () => undefined),
      },
    })

    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_final",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "final_answer",
          displayRole: "assistant",
          stepId: null,
          stepNumber: null,
          contentText: rawMarkdown,
          createdAt: "2026-03-24T12:00:00Z",
        },
      ]
    )

    expect(screen.getByRole("heading", { level: 3, name: "请假流程" })).toBeInTheDocument()
    expect(screen.getByText(/请假前/)).toBeInTheDocument()

    const link = screen.getByRole("link", { name: "查看文档" })
    expect(link).toHaveAttribute("href", "https://example.com/policy")
    expect(link).toHaveAttribute("target", "_blank")
    expect(link).toHaveAttribute("rel", "noreferrer noopener")

    expect(screen.getByRole("table")).toBeInTheDocument()
    expect(screen.getByText("console.log('hello')")).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy assistant message" }))
      await flushMicrotasks()
    })
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(rawMarkdown)
  })

  it("keeps planning entries as plain text with fenced code parsing only", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      [
        {
          id: "entry_plan",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "planning",
          displayRole: "assistant",
          stepId: "step-1",
          stepNumber: null,
          contentText: "### 计划标题\n- 第一项",
          createdAt: "2026-03-24T12:00:00Z",
        },
      ]
    )

    expect(screen.queryByRole("heading", { level: 3, name: "计划标题" })).not.toBeInTheDocument()
    expect(screen.queryByRole("listitem", { name: "第一项" })).not.toBeInTheDocument()
    expect(
      screen.getByText((content) => content.includes("### 计划标题") && content.includes("- 第一项"))
    ).toBeInTheDocument()
  })
})
