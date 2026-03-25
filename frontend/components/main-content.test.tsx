import { fireEvent, render, screen } from "@testing-library/react"

import { MainContent } from "@/components/main-content"
import type { RunHistoryStep } from "@/lib/sidecar-client"
import type { ModelOptionGroup, SelectedModelRef } from "@/lib/provider-settings"

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

function renderMainContent(selection: SelectedModelRef | null, runStepsByRunId: Record<string, RunHistoryStep[]> = {}) {
  const onSelectedModelChange = vi.fn()

  render(
    <MainContent
      activeProject={{ id: 1, name: "nutstore-bot", path: "/tmp/nutstore-bot", sessions: [] }}
      activeSession={{
        id: "sess_1",
        workspaceId: "ws_1",
        title: "New session",
        titleSource: "placeholder",
        createdAt: "2026-03-24T12:00:00Z",
        updatedAt: "2026-03-24T12:00:00Z",
        lastMessageAt: null,
        messageCount: 0,
        lastMessagePreview: null,
        activeConnectionId: null,
        activeModelId: null,
        messages: runStepsByRunId.run_1
          ? [
              {
                id: "msg_1",
                role: "user",
                content: "Inspect the workspace",
                createdAt: "2026-03-24T12:00:00Z",
                runId: "run_1",
                stepId: null,
              },
            ]
          : [],
      }}
      runStepsByRunId={runStepsByRunId}
      onSendMessage={vi.fn()}
      modelOptionGroups={groups}
      selectedModel={selection}
      selectedReasoningEffort={null}
      onSelectedModelChange={onSelectedModelChange}
      onSelectedReasoningEffortChange={vi.fn()}
      isLoadingModels={false}
      providerError={null}
      runError={null}
    />
  )

  return { onSelectedModelChange }
}

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
        activeProject={{ id: 1, name: "nutstore-bot", path: "/tmp/nutstore-bot", sessions: [] }}
        activeSession={{
          id: "sess_1",
          workspaceId: "ws_1",
          title: "New session",
          titleSource: "placeholder",
          createdAt: "2026-03-24T12:00:00Z",
          updatedAt: "2026-03-24T12:00:00Z",
          lastMessageAt: null,
          messageCount: 0,
          lastMessagePreview: null,
          activeConnectionId: null,
          activeModelId: null,
          messages: [],
        }}
        runStepsByRunId={{}}
        onSendMessage={vi.fn()}
        modelOptionGroups={[]}
        selectedModel={null}
        selectedReasoningEffort={null}
        onSelectedModelChange={vi.fn()}
        onSelectedReasoningEffortChange={vi.fn()}
        isLoadingModels={false}
        providerError={null}
        runError={null}
      />
    )

    expect(screen.getByText("No configured providers").closest("button")).toBeDisabled()
    expect(screen.getByLabelText("Send")).toBeDisabled()
  })

  it("shows reasoning effort selector when the selected model supports it", () => {
    renderMainContent({ connectionId: "prov_openai", modelId: "gpt-5.4" })

    expect(screen.getByLabelText("Reasoning effort")).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Auto" })).toBeInTheDocument()
  })

  it("renders persisted planning and action steps with action display priority", () => {
    renderMainContent(
      { connectionId: "prov_openai", modelId: "gpt-5.4" },
      {
        run_1: [
          {
            id: "step_plan",
            runId: "run_1",
            sessionId: "sess_1",
            sequenceNo: 1,
            stepId: "step-1",
            stepKind: "planning",
            stepNumber: null,
            plan: "Inspect the repo and identify the main entry points.",
            usage: { inputTokens: 10, outputTokens: 5, reasoningTokens: 0 },
            durationMs: 120,
            hasDelta: true,
            createdAt: "2026-03-24T12:00:01Z",
          },
          {
            id: "step_action",
            runId: "run_1",
            sessionId: "sess_1",
            sequenceNo: 2,
            stepId: "step-2",
            stepKind: "action",
            stepNumber: 1,
            codeAction: 'print("hello")',
            actionOutput: { result: "hello" },
            observations: ["Execution logs:", "hello"],
            error: "Minor warning",
            usage: { inputTokens: 12, outputTokens: 4, reasoningTokens: 0 },
            durationMs: 180,
            hasDelta: true,
            createdAt: "2026-03-24T12:00:02Z",
          },
        ],
      }
    )

    expect(screen.getByText("Planning step")).toBeInTheDocument()
    expect(screen.getByText("Inspect the repo and identify the main entry points.")).toBeInTheDocument()
    expect(screen.getByText("Step 1")).toBeInTheDocument()
    expect(screen.getByText("print(\"hello\")")).toBeInTheDocument()
    expect(screen.getByText((content) => content.includes('"result": "hello"'))).toBeInTheDocument()
    expect(screen.queryByText("Execution logs:\nhello")).not.toBeInTheDocument()
    expect(screen.getByText("Minor warning")).toBeInTheDocument()
  })
})
