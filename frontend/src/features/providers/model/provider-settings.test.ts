import {
  formFromConnection,
  getModelOptionLabel,
  getReasoningEffortOptions,
  getSelectedModelOption,
  isSelectedModelAvailable,
  normalizeSelectedReasoningEffort,
  type ModelOptionGroup,
  type ProviderConnectionDetail,
} from "@/features/providers"

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
    connectionId: "prov_custom",
    providerLabel: "Team Gateway",
    providerId: "team-gateway",
    models: [
      {
        connectionId: "prov_custom",
        providerLabel: "Team Gateway",
        providerId: "team-gateway",
        modelId: "gpt-5.4",
        label: "Team GPT",
        supportsReasoningTokens: false,
      },
    ],
  },
]

describe("provider-settings helpers", () => {
  it("marks stored API keys without exposing the raw value in edit forms", () => {
    const form = formFromConnection({
      id: "prov_openai",
      kind: "builtin",
      runtimeProvider: "openai",
      catalogProviderId: "openai",
      displayName: "OpenAI",
      baseUrl: null,
      apiKeyConfigured: true,
      preferredModelId: "gpt-5.4",
      enabledModelIds: ["gpt-5.4"],
      updatedAt: "2026-04-15T00:00:00Z",
      modelPolicy: "all_catalog",
      customModels: [],
    } satisfies ProviderConnectionDetail)

    expect(form.apiKey).toBe("")
    expect(form.hasStoredApiKey).toBe(true)
  })

  it("checks selected model availability by connection and model id", () => {
    expect(
      isSelectedModelAvailable(
        { connectionId: "prov_custom", modelId: "gpt-5.4" },
        groups
      )
    ).toBe(true)

    expect(
      isSelectedModelAvailable(
        { connectionId: "prov_missing", modelId: "gpt-5.4" },
        groups
      )
    ).toBe(false)
  })

  it("builds a grouped display label for the selected model", () => {
    expect(
      getModelOptionLabel(
        { connectionId: "prov_custom", modelId: "gpt-5.4" },
        groups
      )
    ).toBe("Team Gateway - Team GPT")
  })

  it("returns the selected model option details", () => {
    expect(
      getSelectedModelOption(
        { connectionId: "prov_openai", modelId: "gpt-5.4" },
        groups
      )?.model.reasoningEffortValues
    ).toEqual(["none", "low", "medium", "high"])
  })

  it("returns reasoning effort options for the selected model", () => {
    expect(
      getReasoningEffortOptions(
        { connectionId: "prov_openai", modelId: "gpt-5.4" },
        groups
      )
    ).toEqual(["none", "low", "medium", "high"])
    expect(
      getReasoningEffortOptions(
        { connectionId: "prov_custom", modelId: "gpt-5.4" },
        groups
      )
    ).toEqual([])
  })

  it("normalizes reasoning effort against model availability", () => {
    expect(
      normalizeSelectedReasoningEffort(
        { connectionId: "prov_openai", modelId: "gpt-5.4" },
        groups,
        "medium"
      )
    ).toBe("medium")
    expect(
      normalizeSelectedReasoningEffort(
        { connectionId: "prov_openai", modelId: "gpt-5.4" },
        groups,
        "xhigh"
      )
    ).toBeNull()
  })
})
