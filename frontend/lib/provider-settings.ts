export interface Model {
  id: string
  modelId: string
  displayName: string
}

export interface Header {
  id: string
  name: string
  value: string
}

export interface ProviderConfig {
  providerId: string
  displayName: string
  baseUrl: string
  apiKey: string
  models: Model[]
  headers: Header[]
}

export interface ConnectedProvider {
  id: string
  name: string
  icon: string
  description: string
  modelCount: number
  config: ProviderConfig
}

export const createEmptyProviderConfig = (): ProviderConfig => ({
  providerId: "",
  displayName: "",
  baseUrl: "",
  apiKey: "",
  models: [{ id: "1", modelId: "", displayName: "" }],
  headers: [],
})

export const cloneProviderConfig = (config: ProviderConfig): ProviderConfig => ({
  providerId: config.providerId,
  displayName: config.displayName,
  baseUrl: config.baseUrl,
  apiKey: config.apiKey,
  models: config.models.map((model) => ({ ...model })),
  headers: config.headers.map((header) => ({ ...header })),
})