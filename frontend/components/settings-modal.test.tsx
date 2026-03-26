import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import { SettingsModal } from "@/components/settings-modal"
import type { ProviderCatalogEntry, ProviderConnectionDetail } from "@/lib/provider-settings"

const providerCatalog: ProviderCatalogEntry[] = [
  {
    id: "openai",
    label: "OpenAI / Compatible",
    kind: "builtin",
    runtimeProvider: "openai",
    baseUrlPolicy: "optional",
    models: [{ id: "gpt-5.4", supportsReasoningTokens: true }],
  },
  {
    id: "gemini",
    label: "Gemini",
    kind: "builtin",
    runtimeProvider: "gemini",
    baseUrlPolicy: "hidden",
    models: [{ id: "gemini/gemini-2.5-pro", supportsReasoningTokens: true }],
  },
  {
    id: "custom",
    label: "Custom OpenAI-Compatible",
    kind: "custom-template",
    runtimeProvider: "custom",
    baseUrlPolicy: "required",
    models: [],
  },
]

describe("SettingsModal provider config", () => {
  it("hides base URL when configuring Gemini", async () => {
    render(
      <SettingsModal
        isOpen={true}
        onClose={vi.fn()}
        providerCatalog={providerCatalog}
        providerConnections={[]}
        onSaveProvider={vi.fn(async () => undefined)}
        onRemoveProvider={vi.fn(async () => undefined)}
      />
    )

    fireEvent.click(screen.getAllByRole("button", { name: "+ Connect" })[1])

    await waitFor(() => {
      expect(screen.getByText("Connect Gemini")).toBeInTheDocument()
    })

    expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument()
  })

  it("shows provider health status badges for connected providers", () => {
    const providerConnections: ProviderConnectionDetail[] = [
      {
        id: "prov_openai",
        kind: "builtin",
        runtimeProvider: "openai",
        catalogProviderId: "openai",
        displayName: "OpenAI",
        baseUrl: null,
        apiKeyConfigured: true,
        healthStatus: "connected",
        healthMessage: "Validation succeeded",
        lastValidatedAt: "2026-03-24T12:00:00Z",
        preferredModelId: "gpt-5.4",
        enabledModelIds: ["gpt-5.4"],
        updatedAt: "2026-03-24T12:00:00Z",
        modelPolicy: "all_catalog",
        customModels: [],
        headers: [],
      },
    ]

    render(
      <SettingsModal
        isOpen={true}
        onClose={vi.fn()}
        providerCatalog={providerCatalog}
        providerConnections={providerConnections}
        onSaveProvider={vi.fn(async () => undefined)}
        onRemoveProvider={vi.fn(async () => undefined)}
      />
    )

    expect(screen.getByText("Connected")).toBeInTheDocument()
    expect(screen.getByText("Validation succeeded")).toBeInTheDocument()
  })

  it("blocks submit when non-secret fields contain sensitive-looking content", async () => {
    const onSaveProvider = vi.fn(async () => undefined)

    render(
      <SettingsModal
        isOpen={true}
        onClose={vi.fn()}
        providerCatalog={providerCatalog}
        providerConnections={[]}
        onSaveProvider={onSaveProvider}
        onRemoveProvider={vi.fn(async () => undefined)}
      />
    )

    fireEvent.click(screen.getByRole("button", { name: /Configure custom provider/i }))

    fireEvent.change(screen.getByPlaceholderText("provider-id"), {
      target: { value: "minimax" },
    })
    fireEvent.change(screen.getByPlaceholderText("My AI Provider"), {
      target: { value: 'MiniMax apiKey="sk-sensitive-123456"' },
    })
    fireEvent.change(screen.getByPlaceholderText("https://api.myprovider.com/v1"), {
      target: { value: "https://api.minimaxi.com/v1" },
    })
    fireEvent.change(screen.getByPlaceholderText("model-id"), {
      target: { value: "MiniMax-M2.7-highspeed" },
    })

    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))

    await waitFor(() => {
      expect(
        screen.getByText(
          "Sensitive data detected in non-secret fields. Move keys/tokens to API key or secret headers."
        )
      ).toBeInTheDocument()
    })
    expect(onSaveProvider).not.toHaveBeenCalled()
  })
})
