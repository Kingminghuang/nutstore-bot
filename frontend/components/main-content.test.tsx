import { fireEvent, render, screen } from "@testing-library/react"

import { MainContent } from "@/components/main-content"
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

function renderMainContent(selection: SelectedModelRef | null) {
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
        messages: [],
      }}
      onSendMessage={vi.fn()}
      modelOptionGroups={groups}
      selectedModel={selection}
      onSelectedModelChange={onSelectedModelChange}
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
        onSendMessage={vi.fn()}
        modelOptionGroups={[]}
        selectedModel={null}
        onSelectedModelChange={vi.fn()}
        isLoadingModels={false}
        providerError={null}
        runError={null}
      />
    )

    expect(screen.getByText("No configured providers").closest("button")).toBeDisabled()
    expect(screen.getByLabelText("Send")).toBeDisabled()
  })
})
