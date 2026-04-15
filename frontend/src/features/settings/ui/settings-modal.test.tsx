import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import { SettingsModal } from "@/features/settings"
import { STORED_API_KEY_MASK, type ProviderCatalogEntry, type ProviderConnectionDetail } from "@/features/providers"

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
      expect(screen.getByText("Gemini Configuration")).toBeInTheDocument()
    })

    expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Gemini API key")).toBeInTheDocument()
  })

  it("creates a builtin provider from a single configuration screen", async () => {
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

    fireEvent.click(screen.getAllByRole("button", { name: "+ Connect" })[0])
    await screen.findByText("OpenAI / Compatible Configuration")

    fireEvent.change(screen.getByLabelText("OpenAI / Compatible API key"), {
      target: { value: "sk-openai" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Connect provider" }))

    await waitFor(() => {
      expect(onSaveProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "builtin",
          catalogProviderId: "openai",
          apiKey: "sk-openai",
        }),
        undefined
      )
    })
  })

  it("creates a custom provider from a single configuration screen", async () => {
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
    await screen.findByText("Custom OpenAI-Compatible Configuration")

    fireEvent.change(screen.getByLabelText("Provider ID"), {
      target: { value: "minimax" },
    })
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "MiniMax" },
    })
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://api.minimax.example/v1" },
    })
    fireEvent.change(screen.getByPlaceholderText("model-id"), {
      target: { value: "MiniMax-M2.7-highspeed" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }))

    await waitFor(() => {
      expect(onSaveProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "custom",
          customSlug: "minimax",
          displayName: "MiniMax",
          baseUrl: "https://api.minimax.example/v1",
          customModels: [
            expect.objectContaining({
              modelId: "MiniMax-M2.7-highspeed",
              enabled: true,
            }),
          ],
        }),
        undefined
      )
    })
  })

  it("shows a masked API key for existing providers and preserves the stored key when unchanged", async () => {
    const onSaveProvider = vi.fn(async () => undefined)
    const providerConnections: ProviderConnectionDetail[] = [
      {
        id: "prov_openai",
        kind: "builtin",
        runtimeProvider: "openai",
        catalogProviderId: "openai",
        displayName: "OpenAI",
        baseUrl: null,
        apiKeyConfigured: true,
        preferredModelId: "gpt-5.4",
        enabledModelIds: ["gpt-5.4"],
        updatedAt: "2026-03-24T12:00:00Z",
        modelPolicy: "all_catalog",
        customModels: [],
      },
    ]

    render(
      <SettingsModal
        isOpen={true}
        onClose={vi.fn()}
        providerCatalog={providerCatalog}
        providerConnections={providerConnections}
        onSaveProvider={onSaveProvider}
        onRemoveProvider={vi.fn(async () => undefined)}
      />
    )

    fireEvent.click(screen.getByText("OpenAI"))

    await waitFor(() => {
      expect(screen.getByText("OpenAI Configuration")).toBeInTheDocument()
    })

    const input = screen.getByLabelText("API key") as HTMLInputElement
    expect(input.value).toBe(STORED_API_KEY_MASK)
    expect(input).toHaveAttribute("readonly")
    expect(screen.getByRole("button", { name: "Replace API key" })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Save provider" }))

    await waitFor(() => {
      expect(onSaveProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "builtin",
          apiKey: undefined,
        }),
        "prov_openai"
      )
    })
  })

  it("submits a replacement API key after the masked field is unlocked", async () => {
    const onSaveProvider = vi.fn(async () => undefined)
    const providerConnections: ProviderConnectionDetail[] = [
      {
        id: "prov_openai",
        kind: "builtin",
        runtimeProvider: "openai",
        catalogProviderId: "openai",
        displayName: "OpenAI",
        baseUrl: null,
        apiKeyConfigured: true,
        preferredModelId: "gpt-5.4",
        enabledModelIds: ["gpt-5.4"],
        updatedAt: "2026-03-24T12:00:00Z",
        modelPolicy: "all_catalog",
        customModels: [],
      },
    ]

    render(
      <SettingsModal
        isOpen={true}
        onClose={vi.fn()}
        providerCatalog={providerCatalog}
        providerConnections={providerConnections}
        onSaveProvider={onSaveProvider}
        onRemoveProvider={vi.fn(async () => undefined)}
      />
    )

    fireEvent.click(screen.getByText("OpenAI"))
    await screen.findByText("OpenAI Configuration")

    fireEvent.click(screen.getByRole("button", { name: "Replace API key" }))
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-replaced-key" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }))

    await waitFor(() => {
      expect(onSaveProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "builtin",
          apiKey: "sk-replaced-key",
        }),
        "prov_openai"
      )
    })
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
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: 'MiniMax apiKey="sk-sensitive-123456"' },
    })
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://api.minimaxi.com/v1" },
    })
    fireEvent.change(screen.getByPlaceholderText("model-id"), {
      target: { value: "MiniMax-M2.7-highspeed" },
    })

    fireEvent.click(screen.getByRole("button", { name: "Save provider" }))

    await waitFor(() => {
      expect(
        screen.getByText(
          "Sensitive data detected in non-secret fields. Move keys/tokens to the API key field."
        )
      ).toBeInTheDocument()
    })
    expect(onSaveProvider).not.toHaveBeenCalled()
  })
})
