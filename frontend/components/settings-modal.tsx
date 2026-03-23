"use client"

import { useState } from "react"
import { ArrowLeft, X, Trash2, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"
import {
  cloneProviderConfig,
  createEmptyProviderConfig,
  type ConnectedProvider,
  type Header,
  type Model,
  type ProviderConfig,
} from "@/lib/provider-settings"

type SettingsPage = "providers" | "custom-provider" | "provider-config" | "connect-provider"

type FieldErrorKey = "providerId" | "displayName" | "baseUrl" | "apiKey" | "models"

type FieldErrors = Partial<Record<FieldErrorKey, string>>

interface SettingsModalProps {
  isOpen: boolean
  onClose: () => void
  connectedProviders: ConnectedProvider[]
  onSaveConnectedProvider: (provider: ConnectedProvider, previousProviderId?: string) => void
  onRemoveConnectedProvider: (providerId: string) => void
}

const providers = [
  { id: "anthropic", name: "Anthropic", icon: "A", description: "Direct access to Claude models, including Pro and Max" },
  { id: "openai", name: "OpenAI", icon: "O", description: "GPT models for fast, capable general AI tasks" },
  { id: "google", name: "Google", icon: "*", description: "Google's AI models including Gemini" },
]

const normalizeProviderConfig = (
  config: ProviderConfig,
  selectedProviderData?: (typeof providers)[number],
): ProviderConfig => ({
  providerId: config.providerId.trim() || selectedProviderData?.id || "",
  displayName: config.displayName.trim() || selectedProviderData?.name || "",
  baseUrl: config.baseUrl.trim(),
  apiKey: config.apiKey.trim(),
  models: config.models.map((model) => ({
    ...model,
    modelId: model.modelId.trim(),
    displayName: model.displayName.trim(),
  })),
  headers: config.headers.map((header) => ({
    ...header,
    name: header.name.trim(),
    value: header.value.trim(),
  })),
})

const createConnectedProvider = (
  config: ProviderConfig,
  selectedProviderData?: (typeof providers)[number]
): ConnectedProvider => {
  const normalizedConfig = normalizeProviderConfig(config, selectedProviderData)
  const modelCount = normalizedConfig.models.filter((model) => model.modelId).length
  const fallbackName = normalizedConfig.displayName || normalizedConfig.providerId || "Custom provider"

  return {
    id: normalizedConfig.providerId || fallbackName.toLowerCase().replace(/\s+/g, "-"),
    name: normalizedConfig.displayName || selectedProviderData?.name || fallbackName,
    icon: selectedProviderData?.icon ?? (fallbackName.charAt(0).toUpperCase() || "C"),
    description:
      normalizedConfig.baseUrl || `${modelCount || 1} model${modelCount === 1 ? "" : "s"} configured`,
    modelCount,
    config: normalizedConfig,
  }
}

const inputClassName = (hasError: boolean) =>
  cn(
    "w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-1",
    hasError
      ? "border-destructive focus:ring-destructive/30"
      : "border-[#e8e4e0] focus:ring-foreground/20"
  )

function SubmitButton({
  label,
  loadingLabel,
  isSubmitting,
  disabled,
  error,
  onClick,
}: {
  label: string
  loadingLabel: string
  isSubmitting: boolean
  disabled: boolean
  error?: string | null
  onClick: () => void
}) {
  return (
    <div className="pt-2">
      {error && <p className="mb-3 text-sm text-destructive">{error}</p>}
      <div className="flex justify-end">
        <Button onClick={onClick} disabled={disabled} aria-busy={isSubmitting}>
          {isSubmitting && <Spinner className="size-4" />}
          {isSubmitting ? loadingLabel : label}
        </Button>
      </div>
    </div>
  )
}

export function SettingsModal({
  isOpen,
  onClose,
  connectedProviders,
  onSaveConnectedProvider,
  onRemoveConnectedProvider,
}: SettingsModalProps) {
  const [currentPage, setCurrentPage] = useState<SettingsPage>("providers")
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null)
  const [config, setConfig] = useState<ProviderConfig>(createEmptyProviderConfig)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  if (!isOpen) return null

  const clearErrors = (...keys: FieldErrorKey[]) => {
    setSubmitError(null)
    if (keys.length === 0) {
      setFieldErrors({})
      return
    }
    setFieldErrors((prev) => {
      const next = { ...prev }
      for (const key of keys) {
        delete next[key]
      }
      return next
    })
  }

  const resetModal = () => {
    setCurrentPage("providers")
    setSelectedProvider(null)
    setEditingProviderId(null)
    setConfig(createEmptyProviderConfig())
    setFieldErrors({})
    setSubmitError(null)
    setIsSubmitting(false)
  }

  const handleModalClose = () => {
    resetModal()
    onClose()
  }

  const handleBackClick = () => {
    if (currentPage === "custom-provider" || currentPage === "provider-config" || currentPage === "connect-provider") {
      clearErrors()
      setIsSubmitting(false)
      setCurrentPage("providers")
    } else {
      handleModalClose()
    }
  }

  const handleProviderSelect = (providerId: string) => {
    clearErrors()
    setSelectedProvider(providerId)
    setEditingProviderId(null)

    if (providerId === "custom") {
      setConfig(createEmptyProviderConfig())
      setCurrentPage("custom-provider")
      return
    }

    const selectedProviderData = providers.find((provider) => provider.id === providerId)
    const existingProvider = connectedProviders.find((provider) => provider.id === providerId)

    if (existingProvider) {
      setEditingProviderId(existingProvider.id)
      setConfig(cloneProviderConfig(existingProvider.config))
      setCurrentPage("provider-config")
      return
    }

    setConfig({
      ...createEmptyProviderConfig(),
      providerId: selectedProviderData?.id ?? "",
      displayName: selectedProviderData?.name ?? "",
    })
    setCurrentPage("connect-provider")
  }

  const getSelectedProviderData = () => {
    return providers.find((p) => p.id === selectedProvider)
  }

  const handleConnectedProviderClick = (provider: ConnectedProvider) => {
    clearErrors()
    setSelectedProvider(provider.id)
    setEditingProviderId(provider.id)
    setConfig(cloneProviderConfig(provider.config))
    setCurrentPage("provider-config")
    setIsSubmitting(false)
  }

  const handleDisconnectProvider = (providerId: string) => {
    onRemoveConnectedProvider(providerId)
    if (selectedProvider === providerId && currentPage === "provider-config") {
      setSelectedProvider(null)
      setConfig(createEmptyProviderConfig())
      setCurrentPage("providers")
      clearErrors()
      setIsSubmitting(false)
    }
  }

  const isProviderConnected = (providerId: string) => {
    return connectedProviders.some((provider) => provider.id === providerId)
  }

  const getConnectedProvider = (providerId: string) => {
    return connectedProviders.find((provider) => provider.id === providerId)
  }

  const getSelectedProviderLabel = () => {
    return config.displayName.trim() || getSelectedProviderData()?.name || config.providerId.trim() || "Provider"
  }

  const isCustomProvider = () => !getSelectedProviderData()

  const updateConfigField = (field: keyof ProviderConfig, value: string) => {
    setConfig((prev) => ({ ...prev, [field]: value }))
    if (field === "providerId" || field === "displayName" || field === "baseUrl" || field === "apiKey") {
      clearErrors(field)
    } else {
      setSubmitError(null)
    }
  }

  const handleAddModel = () => {
    clearErrors("models")
    setConfig((prev) => ({
      ...prev,
      models: [...prev.models, { id: Date.now().toString(), modelId: "", displayName: "" }],
    }))
  }

  const handleRemoveModel = (id: string) => {
    clearErrors("models")
    setConfig((prev) => ({
      ...prev,
      models: prev.models.filter((m) => m.id !== id),
    }))
  }

  const handleAddHeader = () => {
    setSubmitError(null)
    setConfig((prev) => ({
      ...prev,
      headers: [...prev.headers, { id: Date.now().toString(), name: "", value: "" }],
    }))
  }

  const handleRemoveHeader = (id: string) => {
    setSubmitError(null)
    setConfig((prev) => ({
      ...prev,
      headers: prev.headers.filter((h) => h.id !== id),
    }))
  }

  const updateModel = (id: string, field: string, value: string) => {
    clearErrors("models")
    setConfig((prev) => ({
      ...prev,
      models: prev.models.map((m) => (m.id === id ? { ...m, [field]: value } : m)),
    }))
  }

  const updateHeader = (id: string, field: string, value: string) => {
    setSubmitError(null)
    setConfig((prev) => ({
      ...prev,
      headers: prev.headers.map((h) => (h.id === id ? { ...h, [field]: value } : h)),
    }))
  }

  const validateCurrentPage = (): FieldErrors => {
    const nextErrors: FieldErrors = {}
    const hasModelId = config.models.some((model) => model.modelId.trim())

    if (currentPage === "custom-provider") {
      if (!config.providerId.trim()) {
        nextErrors.providerId = "Provider ID is required."
      }
      if (!config.displayName.trim()) {
        nextErrors.displayName = "Display name is required."
      }
      if (!config.baseUrl.trim()) {
        nextErrors.baseUrl = "Base URL is required."
      }
      if (!hasModelId) {
        nextErrors.models = "Add at least one model ID before continuing."
      }
    }

    if (currentPage === "connect-provider" && !config.apiKey.trim()) {
      nextErrors.apiKey = `${getSelectedProviderLabel()} API key is required.`
    }

    if (currentPage === "provider-config" && !hasModelId) {
      nextErrors.models = "Add at least one model ID before saving."
    }

    if (currentPage === "provider-config" && isCustomProvider()) {
      if (!config.providerId.trim()) {
        nextErrors.providerId = "Provider ID is required."
      }
      if (!config.baseUrl.trim()) {
        nextErrors.baseUrl = "Base URL is required."
      }
    }

    return nextErrors
  }

  const getSubmitLabels = () => {
    if (currentPage === "custom-provider") {
      return { label: "Save and continue", loadingLabel: "Saving..." }
    }
    if (currentPage === "connect-provider") {
      return { label: "Connect and continue", loadingLabel: "Connecting..." }
    }
    return { label: "Save provider", loadingLabel: "Saving..." }
  }

  const handleSubmit = async () => {
    const nextErrors = validateCurrentPage()

    if (Object.keys(nextErrors).length > 0) {
      setFieldErrors(nextErrors)
      setSubmitError("Fix the highlighted fields and try again.")
      return
    }

    setIsSubmitting(true)
    setFieldErrors({})
    setSubmitError(null)

    await new Promise((resolve) => window.setTimeout(resolve, 450))

    if (currentPage === "custom-provider") {
      setSelectedProvider(config.providerId.trim())
      setEditingProviderId(config.providerId.trim())
      setConfig((prev) => normalizeProviderConfig(prev))
      setCurrentPage("provider-config")
      setIsSubmitting(false)
      return
    }

    if (currentPage === "connect-provider") {
      setConfig((prev) => normalizeProviderConfig(prev, getSelectedProviderData()))
      setCurrentPage("provider-config")
      setIsSubmitting(false)
      return
    }

    const savedProvider = createConnectedProvider(config, getSelectedProviderData())
    onSaveConnectedProvider(savedProvider, editingProviderId ?? undefined)
    handleModalClose()
  }

  const submitLabels = getSubmitLabels()

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-background rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-[#e8e4e0]">
          {currentPage !== "providers" && (
            <button
              onClick={handleBackClick}
              className="p-1 hover:bg-[#efe9e4] rounded transition-colors"
              aria-label="Go back"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
          )}
          {currentPage === "providers" && <div />}
          {currentPage === "providers" && <h2 className="text-lg font-medium">Providers</h2>}
          {currentPage === "custom-provider" && <h2 className="text-lg font-medium">Custom provider</h2>}
          {currentPage === "provider-config" && <h2 className="text-lg font-medium">{getSelectedProviderLabel()} Configuration</h2>}
          {currentPage === "connect-provider" && <h2 className="text-lg font-medium">Connect provider</h2>}
          <button
            onClick={handleModalClose}
            className="p-1 hover:bg-[#efe9e4] rounded transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {currentPage === "providers" && (
            <div className="space-y-8">
              {/* Connected providers */}
              <div>
                <h3 className="text-sm font-medium mb-3">Connected providers</h3>
                {connectedProviders.length === 0 ? (
                  <div className="p-4 bg-[#f5f2ef] rounded-lg text-sm text-foreground/70">
                    No connected providers
                  </div>
                ) : (
                  <div className="space-y-3">
                    {connectedProviders.map((provider) => (
                      <div
                        key={provider.id}
                        className="flex items-center gap-3 rounded-lg border border-[#e8e4e0] bg-[#f8f5f2] p-2"
                      >
                        <button
                          type="button"
                          onClick={() => handleConnectedProviderClick(provider)}
                          className="flex flex-1 items-center justify-between rounded-lg px-2 py-2 text-left transition-colors hover:bg-background"
                        >
                          <div className="flex items-center gap-3">
                            <span className="flex h-8 w-8 items-center justify-center text-lg font-bold">
                              {provider.icon}
                            </span>
                            <div>
                              <h4 className="text-sm font-medium">{provider.name}</h4>
                              <p className="text-xs text-foreground/60">{provider.description}</p>
                            </div>
                          </div>
                          <span className="rounded-full bg-background px-2.5 py-1 text-xs font-medium text-foreground/70">
                            {provider.modelCount || 1} model{provider.modelCount === 1 ? "" : "s"}
                          </span>
                        </button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          className="text-foreground/60 hover:text-destructive"
                          onClick={() => handleDisconnectProvider(provider.id)}
                          aria-label={`Disconnect ${provider.name}`}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Popular providers */}
              <div>
                <h3 className="text-sm font-medium mb-3">Popular providers</h3>
                <div className="space-y-3">
                  {providers.map((provider) => (
                    <div
                      key={provider.id}
                      className="flex items-center justify-between p-4 hover:bg-[#f5f2ef] rounded-lg transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <span className="w-8 h-8 flex items-center justify-center text-lg font-bold">
                          {provider.icon}
                        </span>
                        <div>
                          <h4 className="text-sm font-medium">{provider.name}</h4>
                          <p className="text-xs text-foreground/60">{provider.description}</p>
                        </div>
                      </div>
                      <button
                        onClick={() => {
                          const existingProvider = getConnectedProvider(provider.id)
                          if (existingProvider) {
                            handleConnectedProviderClick(existingProvider)
                            return
                          }

                          handleProviderSelect(provider.id)
                        }}
                        className="ml-4 px-4 py-2 text-sm text-foreground hover:bg-[#e0d9d3] rounded-lg transition-colors flex-shrink-0 border border-[#e8e4e0]"
                      >
                        {isProviderConnected(provider.id) ? "Edit" : "+ Connect"}
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              {/* Custom provider */}
              <div className="pt-4 border-t border-[#e8e4e0]">
                <button
                  onClick={() => handleProviderSelect("custom")}
                  className="text-sm font-medium text-foreground/80 hover:text-foreground transition-colors"
                >
                  Configure custom provider
                </button>
              </div>
            </div>
          )}

          {currentPage === "custom-provider" && (
            <div className="space-y-6">
              <div>
                <p className="text-sm text-foreground/70 mb-4">
                  Configure an OpenAI-compatible provider. See the{" "}
                  <a href="#" className="underline">
                    provider config docs
                  </a>
                  .
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Provider ID</label>
                <input
                  type="text"
                  placeholder="myprovider"
                  value={config.providerId}
                  onChange={(e) => updateConfigField("providerId", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.providerId)}
                  className={inputClassName(Boolean(fieldErrors.providerId))}
                />
                {fieldErrors.providerId && <p className="mt-1 text-xs text-destructive">{fieldErrors.providerId}</p>}
                <p className="text-xs text-foreground/60 mt-1">
                  Lowercase letters, numbers, hyphens, or underscores
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Display name</label>
                <input
                  type="text"
                  placeholder="My AI Provider"
                  value={config.displayName}
                  onChange={(e) => updateConfigField("displayName", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.displayName)}
                  className={inputClassName(Boolean(fieldErrors.displayName))}
                />
                {fieldErrors.displayName && <p className="mt-1 text-xs text-destructive">{fieldErrors.displayName}</p>}
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Base URL</label>
                <input
                  type="text"
                  placeholder="https://api.myprovider.com/v1"
                  value={config.baseUrl}
                  onChange={(e) => updateConfigField("baseUrl", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.baseUrl)}
                  className={inputClassName(Boolean(fieldErrors.baseUrl))}
                />
                {fieldErrors.baseUrl && <p className="mt-1 text-xs text-destructive">{fieldErrors.baseUrl}</p>}
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">API key</label>
                <input
                  type="password"
                  placeholder="API key"
                  value={config.apiKey}
                  onChange={(e) => updateConfigField("apiKey", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.apiKey)}
                  className={inputClassName(Boolean(fieldErrors.apiKey))}
                />
                {fieldErrors.apiKey && <p className="mt-1 text-xs text-destructive">{fieldErrors.apiKey}</p>}
                <p className="text-xs text-foreground/60 mt-1">
                  Optional. Leave empty if you manage auth via headers.
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Models</label>
                <div className="space-y-2">
                  {config.models.map((model) => (
                    <div key={model.id} className="flex gap-2 items-center">
                      <div className="flex-1">
                        <input
                          type="text"
                          placeholder="model-id"
                          value={model.modelId}
                          onChange={(e) => updateModel(model.id, "modelId", e.target.value)}
                          className={inputClassName(Boolean(fieldErrors.models))}
                        />
                      </div>
                      <div className="flex-1">
                        <input
                          type="text"
                          placeholder="Display Name"
                          value={model.displayName}
                          onChange={(e) => updateModel(model.id, "displayName", e.target.value)}
                          className={inputClassName(false)}
                        />
                      </div>
                      {config.models.length > 1 && (
                        <button
                          onClick={() => handleRemoveModel(model.id)}
                          className="p-2 hover:bg-[#efe9e4] rounded transition-colors flex-shrink-0"
                        >
                          <Trash2 className="w-4 h-4 text-foreground/70" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                {fieldErrors.models && <p className="mt-2 text-xs text-destructive">{fieldErrors.models}</p>}
                <button
                  onClick={handleAddModel}
                  className="mt-3 flex items-center gap-1 text-sm text-foreground/70 hover:text-foreground transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  Add model
                </button>
              </div>

              <SubmitButton
                label={submitLabels.label}
                loadingLabel={submitLabels.loadingLabel}
                isSubmitting={isSubmitting}
                disabled={isSubmitting}
                error={submitError}
                onClick={() => void handleSubmit()}
              />
            </div>
          )}

          {currentPage === "connect-provider" && (
            <div className="space-y-6">
              {/* Title with icon */}
              <div className="flex items-center gap-3">
                <span className="text-xl font-bold">{getSelectedProviderData()?.icon}</span>
                <h3 className="text-lg font-medium">Connect {getSelectedProviderData()?.name}</h3>
              </div>

              {/* Description */}
              <p className="text-sm text-foreground/70">
                Enter your {getSelectedProviderData()?.name} API key to connect your account and use {getSelectedProviderData()?.name} models in OpenCode.
              </p>

              {/* API Key input */}
              <div>
                <label className="block text-sm font-medium mb-2">
                  {getSelectedProviderData()?.name} API key
                </label>
                <input
                  type="password"
                  placeholder="API key"
                  value={config.apiKey}
                  onChange={(e) => updateConfigField("apiKey", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.apiKey)}
                  className={inputClassName(Boolean(fieldErrors.apiKey))}
                />
                {fieldErrors.apiKey && <p className="mt-1 text-xs text-destructive">{fieldErrors.apiKey}</p>}
              </div>

              <SubmitButton
                label={submitLabels.label}
                loadingLabel={submitLabels.loadingLabel}
                isSubmitting={isSubmitting}
                disabled={isSubmitting}
                error={submitError}
                onClick={() => void handleSubmit()}
              />
            </div>
          )}

          {currentPage === "provider-config" && (
            <div className="space-y-6">
              <div>
                <label className="block text-sm font-medium mb-2">Provider ID</label>
                <input
                  type="text"
                  placeholder="provider-id"
                  value={config.providerId}
                  onChange={(e) => updateConfigField("providerId", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.providerId)}
                  disabled={!isCustomProvider()}
                  className={cn(
                    inputClassName(Boolean(fieldErrors.providerId)),
                    !isCustomProvider() && "cursor-not-allowed bg-muted/40 text-foreground/60"
                  )}
                />
                {fieldErrors.providerId && <p className="mt-1 text-xs text-destructive">{fieldErrors.providerId}</p>}
                <p className="text-xs text-foreground/60 mt-1">
                  {isCustomProvider()
                    ? "Used as the identifier for this custom provider."
                    : "Built-in provider IDs are fixed and cannot be changed here."}
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Display name</label>
                <input
                  type="text"
                  placeholder="Provider display name"
                  value={config.displayName}
                  onChange={(e) => updateConfigField("displayName", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.displayName)}
                  className={inputClassName(Boolean(fieldErrors.displayName))}
                />
                {fieldErrors.displayName && <p className="mt-1 text-xs text-destructive">{fieldErrors.displayName}</p>}
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">Base URL</label>
                <input
                  type="text"
                  placeholder="https://api.provider.com/v1"
                  value={config.baseUrl}
                  onChange={(e) => updateConfigField("baseUrl", e.target.value)}
                  aria-invalid={Boolean(fieldErrors.baseUrl)}
                  className={inputClassName(Boolean(fieldErrors.baseUrl))}
                />
                {fieldErrors.baseUrl && <p className="mt-1 text-xs text-destructive">{fieldErrors.baseUrl}</p>}
                <p className="text-xs text-foreground/60 mt-1">
                  Optional for built-in providers. Required for custom providers.
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">API key</label>
                <input
                  type="password"
                  placeholder="API key"
                  value={config.apiKey}
                  onChange={(e) => updateConfigField("apiKey", e.target.value)}
                  className={inputClassName(false)}
                />
                <p className="text-xs text-foreground/60 mt-1">
                  Optional. Leave empty if you manage auth via headers.
                </p>
              </div>

              <div>
                <div className="flex items-center justify-between mb-3">
                  <label className="block text-sm font-medium">Models</label>
                </div>
                <div className="space-y-2">
                  {config.models.map((model) => (
                    <div key={model.id} className="flex gap-2 items-end">
                      <div className="flex-1">
                        <input
                          type="text"
                          placeholder="model-id"
                          value={model.modelId}
                          onChange={(e) => updateModel(model.id, "modelId", e.target.value)}
                          className={inputClassName(Boolean(fieldErrors.models))}
                        />
                      </div>
                      <div className="flex-1">
                        <input
                          type="text"
                          placeholder="Display Name"
                          value={model.displayName}
                          onChange={(e) => updateModel(model.id, "displayName", e.target.value)}
                          className={inputClassName(false)}
                        />
                      </div>
                      {config.models.length > 1 && (
                        <button
                          onClick={() => handleRemoveModel(model.id)}
                          className="p-2 hover:bg-[#efe9e4] rounded transition-colors"
                        >
                          <Trash2 className="w-4 h-4 text-foreground/70" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                {fieldErrors.models && <p className="mt-2 text-xs text-destructive">{fieldErrors.models}</p>}
                <button
                  onClick={handleAddModel}
                  className="mt-3 flex items-center gap-1 text-sm text-foreground/70 hover:text-foreground transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  Add model
                </button>
              </div>

              <div>
                <div className="flex items-center justify-between mb-3">
                  <label className="block text-sm font-medium">Headers (optional)</label>
                </div>
                <div className="space-y-2">
                  {config.headers.map((header) => (
                    <div key={header.id} className="flex gap-2 items-end">
                      <div className="flex-1">
                        <input
                          type="text"
                          placeholder="Header-Name"
                          value={header.name}
                          onChange={(e) => updateHeader(header.id, "name", e.target.value)}
                          className="w-full px-3 py-2 border border-[#e8e4e0] rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-foreground/20"
                        />
                      </div>
                      <div className="flex-1">
                        <input
                          type="text"
                          placeholder="value"
                          value={header.value}
                          onChange={(e) => updateHeader(header.id, "value", e.target.value)}
                          className="w-full px-3 py-2 border border-[#e8e4e0] rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-foreground/20"
                        />
                      </div>
                      <button
                        onClick={() => handleRemoveHeader(header.id)}
                        className="p-2 hover:bg-[#efe9e4] rounded transition-colors"
                      >
                        <Trash2 className="w-4 h-4 text-foreground/70" />
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  onClick={handleAddHeader}
                  className="mt-3 flex items-center gap-1 text-sm text-foreground/70 hover:text-foreground transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  Add header
                </button>
              </div>

              <SubmitButton
                label={submitLabels.label}
                loadingLabel={submitLabels.loadingLabel}
                isSubmitting={isSubmitting}
                disabled={isSubmitting}
                error={submitError}
                onClick={() => void handleSubmit()}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
