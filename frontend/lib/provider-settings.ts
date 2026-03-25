export type ProviderRuntime = "anthropic" | "deepseek" | "gemini" | "openai" | "custom"

export type ProviderCatalogModel = {
  id: string
  supportsReasoningTokens: boolean
  reasoningEffortValues?: string[]
}

export type ProviderCatalogEntry = {
  id: string
  label: string
  kind: "builtin" | "custom-template"
  runtimeProvider: ProviderRuntime
  baseUrlPolicy: "hidden" | "optional" | "required"
  models: ProviderCatalogModel[]
}

export type ProviderHeaderDraft = {
  id: string
  name: string
  valueKind: "plain" | "secret"
  plainValue: string
  secretValueInput: string
  hasStoredSecret: boolean
}

export type ProviderModelDraft = {
  id: string
  modelId: string
  displayName: string
  source: "catalog" | "custom"
  enabled: boolean
}

export type ProviderConnectionSummary = {
  id: string
  kind: "builtin" | "custom"
  runtimeProvider: ProviderRuntime
  catalogProviderId?: string
  displayName: string
  baseUrl: string | null
  apiKeyConfigured: boolean
  healthStatus:
    | "unknown"
    | "connected"
    | "invalid_key"
    | "timeout"
    | "model_unavailable"
    | "invalid_config"
  healthMessage: string | null
  lastValidatedAt: string | null
  preferredModelId: string | null
  enabledModelIds: string[]
  updatedAt: string
}

export type ProviderConnectionDetail = ProviderConnectionSummary & {
  customSlug?: string
  modelPolicy: "all_catalog" | "restricted" | "custom_only"
  customModels: ProviderModelDraft[]
  headers: ProviderHeaderDraft[]
}

export type ModelOption = {
  connectionId: string
  providerLabel: string
  providerId: string
  modelId: string
  label: string
  supportsReasoningTokens: boolean
  reasoningEffortValues?: string[]
}

export type ModelOptionGroup = {
  connectionId: string
  providerLabel: string
  providerId: string
  models: ModelOption[]
}

export type SelectedModelRef = {
  connectionId: string
  modelId: string
}

export type SelectedReasoningEffort = string | null

export type ProviderCatalogResponse = {
  providers: ProviderCatalogEntry[]
}

export type ProviderConnectionsResponse = {
  connections: ProviderConnectionDetail[]
}

export type ModelOptionsResponse = {
  groups: ModelOptionGroup[]
  defaultSelection: SelectedModelRef | null
}

export type ProviderHeaderInput = {
  id?: string
  name: string
  valueKind: "plain" | "secret"
  plainValue?: string
  secretValue?: string | null
}

export type ProviderModelInput = {
  id?: string
  modelId: string
  displayName?: string
  enabled?: boolean
}

export type BuiltinProviderPayload = {
  kind: "builtin"
  catalogProviderId: string
  displayName: string
  baseUrl: string | null
  apiKey?: string | null
  modelPolicy: "all_catalog" | "restricted"
  preferredModelId: string | null
  enabledModelIds: string[]
}

export type CustomProviderPayload = {
  kind: "custom"
  customSlug: string
  displayName: string
  baseUrl: string
  apiKey?: string | null
  preferredModelId: string | null
  customModels: ProviderModelInput[]
  headers: ProviderHeaderInput[]
}

export type SaveProviderPayload = BuiltinProviderPayload | CustomProviderPayload

export type ProviderConnectionForm = {
  providerId: string
  displayName: string
  baseUrl: string
  apiKey: string
  modelPolicy: "all_catalog" | "restricted" | "custom_only"
  preferredModelId: string
  models: ProviderModelDraft[]
  headers: ProviderHeaderDraft[]
}

export const createEmptyProviderConnectionForm = (): ProviderConnectionForm => ({
  providerId: "",
  displayName: "",
  baseUrl: "",
  apiKey: "",
  modelPolicy: "custom_only",
  preferredModelId: "",
  models: [{ id: createLocalId("model"), modelId: "", displayName: "", source: "custom", enabled: true }],
  headers: [],
})

export const cloneProviderConnectionForm = (
  config: ProviderConnectionForm
): ProviderConnectionForm => ({
  providerId: config.providerId,
  displayName: config.displayName,
  baseUrl: config.baseUrl,
  apiKey: config.apiKey,
  modelPolicy: config.modelPolicy,
  preferredModelId: config.preferredModelId,
  models: config.models.map((model) => ({ ...model })),
  headers: config.headers.map((header) => ({ ...header })),
})

export const formFromConnection = (
  connection: ProviderConnectionDetail
): ProviderConnectionForm => ({
  providerId: connection.catalogProviderId ?? connection.customSlug ?? connection.runtimeProvider,
  displayName: connection.displayName,
  baseUrl: connection.baseUrl ?? "",
  apiKey: "",
  modelPolicy: connection.modelPolicy,
  preferredModelId: connection.preferredModelId ?? "",
  models:
    connection.kind === "custom"
      ? connection.customModels.map((model) => ({ ...model }))
      : connection.enabledModelIds.map((modelId, index) => ({
          id: `${connection.id}-${modelId}-${index}`,
          modelId,
          displayName: "",
          source: "catalog",
          enabled: true,
        })),
  headers: connection.headers.map((header) => ({ ...header, plainValue: header.plainValue ?? "", secretValueInput: "" })),
})

export const isSelectedModelAvailable = (
  selection: SelectedModelRef | null,
  groups: ModelOptionGroup[]
): boolean => {
  if (!selection) {
    return false
  }

  return groups.some(
    (group) =>
      group.connectionId === selection.connectionId &&
      group.models.some((model) => model.modelId === selection.modelId)
  )
}

export const getModelOptionLabel = (
  selection: SelectedModelRef | null,
  groups: ModelOptionGroup[]
): string | null => {
  const match = getSelectedModelOption(selection, groups)
  if (!match) {
    return null
  }

  const { group, model } = match
  return `${group.providerLabel} - ${model.label}`
}

export const getSelectedModelOption = (
  selection: SelectedModelRef | null,
  groups: ModelOptionGroup[]
): { group: ModelOptionGroup; model: ModelOption } | null => {
  if (!selection) {
    return null
  }

  const group = groups.find((item) => item.connectionId === selection.connectionId)
  const model = group?.models.find((item) => item.modelId === selection.modelId)
  if (!group || !model) {
    return null
  }

  return { group, model }
}

export const getReasoningEffortOptions = (
  selection: SelectedModelRef | null,
  groups: ModelOptionGroup[]
): string[] => {
  const match = getSelectedModelOption(selection, groups)
  return match?.model.reasoningEffortValues ?? []
}

export const normalizeSelectedReasoningEffort = (
  selection: SelectedModelRef | null,
  groups: ModelOptionGroup[],
  current: SelectedReasoningEffort
): SelectedReasoningEffort => {
  if (!current) {
    return null
  }

  return getReasoningEffortOptions(selection, groups).includes(current) ? current : null
}

export const createLocalId = (prefix: string): string => {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}_${crypto.randomUUID()}`
  }

  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`
}
