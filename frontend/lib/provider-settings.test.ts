import {
  getModelOptionLabel,
  isSelectedModelAvailable,
  type ModelOptionGroup,
} from "@/lib/provider-settings"

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
})
