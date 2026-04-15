"use client"

import { useMemo, useState } from "react"
import { ArrowLeft, Plus, Trash2, X } from "lucide-react"

import { Button } from "@/shared/ui/button"
import { Spinner } from "@/shared/ui/spinner"
import {
  createEmptyProviderConnectionForm,
  createLocalId,
  formFromConnection,
  STORED_API_KEY_MASK,
  type ProviderCatalogEntry,
  type ProviderConnectionDetail,
  type ProviderConnectionForm,
  type ProviderModelDraft,
  type SaveProviderPayload,
} from "@/features/providers"
import { detectSensitiveWriteIssues } from "@/shared/lib"
import { cn } from "@/shared/lib"

type SettingsPage = "providers" | "provider-config"
type FieldErrorKey = "providerId" | "displayName" | "baseUrl" | "apiKey" | "models"
type FieldErrors = Partial<Record<FieldErrorKey, string>>

interface SettingsModalProps {
  isOpen: boolean
  onClose: () => void
  providerCatalog: ProviderCatalogEntry[]
  providerConnections: ProviderConnectionDetail[]
  onSaveProvider: (payload: SaveProviderPayload, providerId?: string) => Promise<void>
  onRemoveProvider: (providerId: string) => Promise<void>
}

const PROVIDER_ICONS: Record<string, string> = {
  anthropic: "A",
  deepseek: "D",
  gemini: "G",
  openai: "O",
  custom: "C",
}

const PROVIDER_DESCRIPTIONS: Record<string, string> = {
  anthropic: "Direct access to Claude models, including Pro and Max",
  deepseek: "DeepSeek chat and reasoning models",
  gemini: "Google Gemini models for multimodal and reasoning tasks",
  openai: "GPT models for fast, capable general AI tasks",
  custom: "Configure any OpenAI-compatible provider with your own base URL",
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
  providerCatalog,
  providerConnections,
  onSaveProvider,
  onRemoveProvider,
}: SettingsModalProps) {
  const [currentPage, setCurrentPage] = useState<SettingsPage>("providers")
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null)
  const [config, setConfig] = useState<ProviderConnectionForm>(createEmptyProviderConnectionForm)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const builtinProviders = useMemo(
    () => providerCatalog.filter((provider) => provider.kind === "builtin"),
    [providerCatalog]
  )
  const customTemplate = useMemo(
    () => providerCatalog.find((provider) => provider.kind === "custom-template") ?? null,
    [providerCatalog]
  )

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
    setConfig(createEmptyProviderConnectionForm())
    setFieldErrors({})
    setSubmitError(null)
    setIsSubmitting(false)
  }

  const handleModalClose = () => {
    resetModal()
    onClose()
  }

  const getSelectedProviderData = () => {
    return providerCatalog.find((provider) => provider.id === selectedProvider) ?? null
  }

  const isCustomProvider = () => getSelectedProviderData()?.kind !== "builtin"
  const selectedProviderData = getSelectedProviderData()

  const normalizeProviderConfig = (
    currentConfig: ProviderConnectionForm,
    selectedCatalog?: ProviderCatalogEntry | null
  ): ProviderConnectionForm => ({
    providerId: currentConfig.providerId.trim() || selectedCatalog?.id || "",
    displayName: currentConfig.displayName.trim() || selectedCatalog?.label || "",
    baseUrl: currentConfig.baseUrl.trim(),
    apiKey: currentConfig.apiKey.trim(),
    hasStoredApiKey: currentConfig.hasStoredApiKey,
    modelPolicy: currentConfig.modelPolicy,
    preferredModelId: currentConfig.preferredModelId.trim(),
    models: currentConfig.models.map((model) => ({
      ...model,
      modelId: model.modelId.trim(),
      displayName: model.displayName.trim(),
    })),
  })

  const handleBackClick = () => {
    if (currentPage === "provider-config") {
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

    if (providerId === customTemplate?.id) {
      setConfig({
        ...createEmptyProviderConnectionForm(),
        modelPolicy: "custom_only",
      })
      setCurrentPage("provider-config")
      return
    }

    const selectedCatalogProvider = providerCatalog.find((provider) => provider.id === providerId)
    const existingProvider = providerConnections.find(
      (provider) => provider.catalogProviderId === providerId
    )

    if (existingProvider) {
      setEditingProviderId(existingProvider.id)
      setConfig(formFromConnection(existingProvider))
      setCurrentPage("provider-config")
      return
    }

    setConfig({
      ...createEmptyProviderConnectionForm(),
      providerId: selectedCatalogProvider?.id ?? "",
      displayName: selectedCatalogProvider?.label ?? "",
      modelPolicy: "all_catalog",
      models:
        selectedCatalogProvider?.models.map((model, index) => ({
          id: `${selectedCatalogProvider.id}-${model.id}-${index}`,
          modelId: model.id,
          displayName: "",
          source: "catalog",
          enabled: true,
        })) ?? [],
      preferredModelId: selectedCatalogProvider?.models[0]?.id ?? "",
    })
    setCurrentPage("provider-config")
  }

  const handleConnectedProviderClick = (provider: ProviderConnectionDetail) => {
    clearErrors()
    setSelectedProvider(provider.catalogProviderId ?? provider.customSlug ?? provider.id)
    setEditingProviderId(provider.id)
    setConfig(formFromConnection(provider))
    setCurrentPage("provider-config")
    setIsSubmitting(false)
  }

  const handleDisconnectProvider = async (providerId: string) => {
    setIsSubmitting(true)
    setSubmitError(null)
    try {
      await onRemoveProvider(providerId)
      if (editingProviderId === providerId) {
        setSelectedProvider(null)
        setConfig(createEmptyProviderConnectionForm())
        setCurrentPage("providers")
      }
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Failed to remove provider")
    } finally {
      setIsSubmitting(false)
    }
  }

  const getSelectedProviderLabel = () => {
    return (
      config.displayName.trim() ||
      selectedProviderData?.label ||
      config.providerId.trim() ||
      "Provider"
    )
  }

  const updateConfigField = (
    field: keyof Pick<ProviderConnectionForm, "providerId" | "displayName" | "baseUrl" | "apiKey" | "preferredModelId">,
    value: string
  ) => {
    setConfig((prev) => ({
      ...prev,
      [field]: value,
      ...(field === "apiKey" ? { hasStoredApiKey: false } : {}),
    }))
    if (field === "providerId" || field === "displayName" || field === "baseUrl" || field === "apiKey") {
      clearErrors(field)
    } else {
      setSubmitError(null)
    }
  }

  const startApiKeyReplacement = () => {
    clearErrors("apiKey")
    setConfig((prev) => ({
      ...prev,
      apiKey: "",
      hasStoredApiKey: false,
    }))
  }

  const updateModelPolicy = (value: "all_catalog" | "restricted" | "custom_only") => {
    setConfig((prev) => ({ ...prev, modelPolicy: value }))
    clearErrors("models")
  }

  const handleAddModel = () => {
    clearErrors("models")
    setConfig((prev) => ({
      ...prev,
      models: [
        ...prev.models,
        {
          id: createLocalId("model"),
          modelId: "",
          displayName: "",
          source: isCustomProvider() ? "custom" : "catalog",
          enabled: true,
        },
      ],
    }))
  }

  const handleRemoveModel = (id: string) => {
    clearErrors("models")
    setConfig((prev) => ({
      ...prev,
      models: prev.models.filter((model) => model.id !== id),
    }))
  }

  const updateModel = (id: string, field: keyof ProviderModelDraft, value: string | boolean) => {
    clearErrors("models")
    setConfig((prev) => ({
      ...prev,
      models: prev.models.map((model) =>
        model.id === id ? { ...model, [field]: value } : model
      ),
    }))
  }

  const validateCurrentPage = (): FieldErrors => {
    const nextErrors: FieldErrors = {}
    const normalizedConfig = normalizeProviderConfig(config, selectedProviderData)
    const hasModelId = normalizedConfig.models.some((model) => model.modelId.trim())

    if (currentPage === "provider-config" && isCustomProvider()) {
      if (!normalizedConfig.providerId) {
        nextErrors.providerId = "Provider ID is required."
      }
      if (!config.displayName.trim()) {
        nextErrors.displayName = "Display name is required."
      }
      if (!normalizedConfig.baseUrl) {
        nextErrors.baseUrl = "Base URL is required."
      }
      if (!hasModelId) {
        nextErrors.models = "Add at least one model ID before saving."
      }
    }

    if (
      currentPage === "provider-config" &&
      !isCustomProvider() &&
      editingProviderId == null &&
      !normalizedConfig.hasStoredApiKey &&
      !normalizedConfig.apiKey
    ) {
      nextErrors.apiKey = `${selectedProviderData?.label ?? getSelectedProviderLabel()} API key is required.`
    }

    if (
      currentPage === "provider-config" &&
      !isCustomProvider() &&
      normalizedConfig.modelPolicy === "restricted" &&
      !hasModelId
    ) {
      nextErrors.models = "Select at least one enabled model before saving."
    }

    return nextErrors
  }

  const getSensitiveFieldError = (normalizedConfig: ProviderConnectionForm): string | null => {
    const issues = detectSensitiveWriteIssues({
      providerId: normalizedConfig.providerId,
      displayName: normalizedConfig.displayName,
      baseUrl: normalizedConfig.baseUrl,
      preferredModelId: normalizedConfig.preferredModelId,
      models: normalizedConfig.models.map((model) => ({
        modelId: model.modelId,
        displayName: model.displayName,
      })),
    })

    if (issues.length === 0) {
      return null
    }

    return "Sensitive data detected in non-secret fields. Move keys/tokens to the API key field."
  }

  const getSubmitLabels = () => {
    if (editingProviderId == null && !isCustomProvider()) {
      return { label: "Connect provider", loadingLabel: "Connecting..." }
    }
    return { label: "Save provider", loadingLabel: "Saving..." }
  }

  const buildSavePayload = (normalizedConfig: ProviderConnectionForm): SaveProviderPayload => {
    if (isCustomProvider()) {
      return {
        kind: "custom",
        customSlug: normalizedConfig.providerId,
        displayName: normalizedConfig.displayName,
        baseUrl: normalizedConfig.baseUrl,
        apiKey: normalizedConfig.hasStoredApiKey ? undefined : normalizedConfig.apiKey || undefined,
        preferredModelId: normalizedConfig.preferredModelId || normalizedConfig.models.find((model) => model.enabled && model.modelId)?.modelId || null,
        customModels: normalizedConfig.models
          .filter((model) => model.modelId)
          .map((model) => ({
            id: model.id,
            modelId: model.modelId,
            displayName: model.displayName || undefined,
            enabled: model.enabled,
          })),
      }
    }

    const enabledModelIds =
      normalizedConfig.modelPolicy === "all_catalog"
        ? []
        : normalizedConfig.models
            .filter((model) => model.enabled && model.modelId)
            .map((model) => model.modelId)

    return {
      kind: "builtin",
      catalogProviderId: selectedProviderData?.id ?? normalizedConfig.providerId,
      displayName: normalizedConfig.displayName,
      baseUrl: selectedProviderData?.baseUrlPolicy === "hidden" ? null : normalizedConfig.baseUrl || null,
      apiKey: normalizedConfig.hasStoredApiKey ? undefined : normalizedConfig.apiKey || undefined,
      modelPolicy: normalizedConfig.modelPolicy === "custom_only" ? "all_catalog" : normalizedConfig.modelPolicy,
      preferredModelId:
        normalizedConfig.preferredModelId ||
        enabledModelIds[0] ||
        selectedProviderData?.models[0]?.id ||
        null,
      enabledModelIds,
    }
  }

  const handleSubmit = async () => {
    const nextErrors = validateCurrentPage()
    const normalizedConfig = normalizeProviderConfig(config, selectedProviderData)
    const sensitiveFieldError = getSensitiveFieldError(normalizedConfig)

    if (Object.keys(nextErrors).length > 0) {
      setFieldErrors(nextErrors)
      setSubmitError("Fix the highlighted fields and try again.")
      return
    }

    if (sensitiveFieldError) {
      setSubmitError(sensitiveFieldError)
      return
    }

    setIsSubmitting(true)
    setFieldErrors({})
    setSubmitError(null)

    try {
      const payload = buildSavePayload(normalizedConfig)
      await onSaveProvider(payload, editingProviderId ?? undefined)
      handleModalClose()
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Failed to save provider")
    } finally {
      setIsSubmitting(false)
    }
  }

  const submitLabels = getSubmitLabels()

  const connectedProviderCards = providerConnections.map((provider) => {
    const key = provider.catalogProviderId ?? provider.customSlug ?? provider.runtimeProvider
    const description =
      provider.kind === "custom"
        ? provider.baseUrl ?? PROVIDER_DESCRIPTIONS.custom
        : PROVIDER_DESCRIPTIONS[key] ?? `${provider.enabledModelIds.length || 1} models configured`
    const modelCount = provider.kind === "custom" ? provider.customModels.length : provider.enabledModelIds.length || 1
    return {
      provider,
      icon: PROVIDER_ICONS[key] ?? key.charAt(0).toUpperCase(),
      description,
      modelCount,
    }
  })

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-background rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto flex flex-col">
        <div className="flex items-center justify-between p-6 border-b border-[#e8e4e0]">
          {currentPage !== "providers" ? (
            <button
              onClick={handleBackClick}
              className="p-1 hover:bg-[#efe9e4] rounded transition-colors"
              aria-label="Go back"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
          ) : (
            <div />
          )}
          {currentPage === "providers" && <h2 className="text-lg font-medium">Providers</h2>}
          {currentPage === "provider-config" && (
            <h2 className="text-lg font-medium">{getSelectedProviderLabel()} Configuration</h2>
          )}
          <button
            onClick={handleModalClose}
            className="p-1 hover:bg-[#efe9e4] rounded transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {currentPage === "providers" && (
            <div className="space-y-8">
              <div>
                <h3 className="text-sm font-medium mb-3">Connected providers</h3>
                {connectedProviderCards.length === 0 ? (
                  <div className="p-4 bg-[#f5f2ef] rounded-lg text-sm text-foreground/70">
                    No connected providers
                  </div>
                ) : (
                  <div className="space-y-3">
                    {connectedProviderCards.map(({ provider, icon, description, modelCount }) => (
                      <div
                        key={provider.id}
                        className="group flex items-center gap-3 rounded-lg border border-[#e8e4e0] bg-[#f8f5f2] p-2"
                      >
                        <button
                          type="button"
                          onClick={() => handleConnectedProviderClick(provider)}
                          className="flex flex-1 items-center justify-between rounded-lg px-2 py-2 text-left transition-colors hover:bg-background"
                        >
                          <div className="flex items-center gap-3">
                            <span className="flex h-8 w-8 items-center justify-center text-lg font-bold">
                              {icon}
                            </span>
                            <div>
                              <h4 className="text-sm font-medium">{provider.displayName}</h4>
                              <p className="text-xs text-foreground/60">{description}</p>
                            </div>
                          </div>
                          <span className="rounded-full bg-background px-2.5 py-1 text-xs font-medium text-foreground/70">
                            {modelCount} model{modelCount === 1 ? "" : "s"}
                          </span>
                        </button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          className="pointer-events-none opacity-0 text-foreground/60 transition-opacity group-hover:opacity-100 group-hover:pointer-events-auto group-focus-within:opacity-100 group-focus-within:pointer-events-auto hover:text-destructive"
                          onClick={() => void handleDisconnectProvider(provider.id)}
                          aria-label={`Disconnect ${provider.displayName}`}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <h3 className="text-sm font-medium mb-3">Popular providers</h3>
                <div className="space-y-3">
                  {builtinProviders.map((provider) => {
                    const existingProvider = providerConnections.find(
                      (connection) => connection.catalogProviderId === provider.id
                    )

                    return (
                      <div
                        key={provider.id}
                        className="flex items-center justify-between p-4 hover:bg-[#f5f2ef] rounded-lg transition-colors"
                      >
                        <div className="flex items-center gap-3">
                          <span className="w-8 h-8 flex items-center justify-center text-lg font-bold">
                            {PROVIDER_ICONS[provider.id] ?? provider.label.charAt(0).toUpperCase()}
                          </span>
                          <div>
                            <h4 className="text-sm font-medium">{provider.label}</h4>
                            <p className="text-xs text-foreground/60">
                              {PROVIDER_DESCRIPTIONS[provider.id] ?? provider.label}
                            </p>
                          </div>
                        </div>
                        <button
                          onClick={() => {
                            if (existingProvider) {
                              handleConnectedProviderClick(existingProvider)
                              return
                            }
                            handleProviderSelect(provider.id)
                          }}
                          className="ml-4 px-4 py-2 text-sm text-foreground hover:bg-[#e0d9d3] rounded-lg transition-colors flex-shrink-0 border border-[#e8e4e0]"
                        >
                          {existingProvider ? "Edit" : "+ Connect"}
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>

              {customTemplate && (
                <div className="pt-4 border-t border-[#e8e4e0]">
                  <button
                    onClick={() => handleProviderSelect(customTemplate.id)}
                    className="text-sm font-medium text-foreground/80 hover:text-foreground transition-colors"
                  >
                    Configure custom provider
                  </button>
                </div>
              )}
            </div>
          )}

          {currentPage === "provider-config" && (
            <div className="space-y-6">
              {!editingProviderId && isCustomProvider() && (
                <p className="text-sm text-foreground/70">
                  Configure an OpenAI-compatible provider and save it from this screen.
                </p>
              )}

              {!editingProviderId && !isCustomProvider() && selectedProviderData && (
                <div className="space-y-3 rounded-lg border border-[#efe9e4] bg-[#f8f5f2] p-4">
                  <div className="flex items-center gap-3">
                    <span className="text-xl font-bold">
                      {PROVIDER_ICONS[selectedProviderData.id] ?? selectedProviderData.label.charAt(0).toUpperCase()}
                    </span>
                    <h3 className="text-lg font-medium">Connect {selectedProviderData.label}</h3>
                  </div>
                  <p className="text-sm text-foreground/70">
                    Enter your {selectedProviderData.label} API key and any model preferences before saving.
                  </p>
                </div>
              )}

              <ProviderIdField
                value={config.providerId}
                error={fieldErrors.providerId}
                disabled={!isCustomProvider()}
                helpText={
                  isCustomProvider()
                    ? "Used as the identifier for this custom provider."
                    : "Built-in provider IDs are fixed and cannot be changed here."
                }
                onChange={(value) => updateConfigField("providerId", value)}
              />

              <TextField
                label="Display name"
                placeholder={isCustomProvider() ? "My AI Provider" : "Provider display name"}
                value={config.displayName}
                error={fieldErrors.displayName}
                onChange={(value) => updateConfigField("displayName", value)}
              />

              {selectedProviderData?.baseUrlPolicy !== "hidden" && (
                <TextField
                  label="Base URL"
                  placeholder="https://api.provider.com/v1"
                  value={config.baseUrl}
                  error={fieldErrors.baseUrl}
                  helpText={
                    isCustomProvider()
                      ? "Required for custom providers."
                      : "Optional for built-in OpenAI-compatible providers."
                  }
                  onChange={(value) => updateConfigField("baseUrl", value)}
                />
              )}

              <PasswordField
                label={
                  !editingProviderId && !isCustomProvider() && selectedProviderData
                    ? `${selectedProviderData.label} API key`
                    : "API key"
                }
                placeholder="API key"
                value={config.apiKey}
                error={fieldErrors.apiKey}
                maskedValue={config.hasStoredApiKey ? STORED_API_KEY_MASK : undefined}
                helpText={
                  config.hasStoredApiKey
                    ? "Stored API key is masked. Replace it only if you want to update the key."
                    : !editingProviderId && !isCustomProvider()
                      ? `Required to connect ${selectedProviderData?.label ?? "this provider"}.`
                    : "Leave empty to keep the existing key unchanged."
                }
                onReplaceMaskedValue={config.hasStoredApiKey ? startApiKeyReplacement : undefined}
                onChange={(value) => updateConfigField("apiKey", value)}
              />

              {!isCustomProvider() && (
                <BuiltinModelPolicyEditor
                  provider={selectedProviderData}
                  config={config}
                  onPolicyChange={updateModelPolicy}
                  onPreferredModelChange={(value) => updateConfigField("preferredModelId", value)}
                  onToggleModel={(modelId, enabled) => {
                    const existing = config.models.find((model) => model.modelId === modelId)
                    if (existing) {
                      updateModel(existing.id, "enabled", enabled)
                      return
                    }

                    setConfig((prev) => ({
                      ...prev,
                      models: [
                        ...prev.models,
                        {
                          id: createLocalId("model"),
                          modelId,
                          displayName: "",
                          source: "catalog",
                          enabled,
                        },
                      ],
                    }))
                    clearErrors("models")
                  }}
                />
              )}

              {isCustomProvider() && (
                <ModelsEditor
                  config={config}
                  fieldError={fieldErrors.models}
                  isCustomProvider={true}
                  onAddModel={handleAddModel}
                  onRemoveModel={handleRemoveModel}
                  onUpdateModel={updateModel}
                />
              )}

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

function ProviderIdField({
  value,
  error,
  disabled,
  helpText,
  onChange,
}: {
  value: string
  error?: string
  disabled?: boolean
  helpText?: string
  onChange: (value: string) => void
}) {
  return (
    <div>
      <label className="block text-sm font-medium mb-2">Provider ID</label>
      <input
        type="text"
        placeholder="provider-id"
        value={value}
        autoComplete="off"
        aria-label="Provider ID"
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={Boolean(error)}
        disabled={disabled}
        className={cn(
          inputClassName(Boolean(error)),
          disabled && "cursor-not-allowed bg-muted/40 text-foreground/60"
        )}
      />
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      {helpText && <p className="text-xs text-foreground/60 mt-1">{helpText}</p>}
    </div>
  )
}

function TextField({
  label,
  placeholder,
  value,
  error,
  helpText,
  onChange,
}: {
  label: string
  placeholder: string
  value: string
  error?: string
  helpText?: string
  onChange: (value: string) => void
}) {
  return (
    <div>
      <label className="block text-sm font-medium mb-2">{label}</label>
      <input
        type="text"
        placeholder={placeholder}
        value={value}
        autoComplete="off"
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={Boolean(error)}
        className={inputClassName(Boolean(error))}
      />
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      {helpText && <p className="text-xs text-foreground/60 mt-1">{helpText}</p>}
    </div>
  )
}

function PasswordField({
  label,
  placeholder,
  value,
  error,
  helpText,
  maskedValue,
  onReplaceMaskedValue,
  onChange,
}: {
  label: string
  placeholder: string
  value: string
  error?: string
  helpText?: string
  maskedValue?: string
  onReplaceMaskedValue?: () => void
  onChange: (value: string) => void
}) {
  const displayValue = maskedValue ?? value

  return (
    <div>
      <label className="block text-sm font-medium mb-2">{label}</label>
      <input
        type="password"
        placeholder={placeholder}
        value={displayValue}
        autoComplete="new-password"
        aria-label={label}
        readOnly={Boolean(maskedValue)}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={Boolean(error)}
        className={inputClassName(Boolean(error))}
      />
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      {helpText && <p className="text-xs text-foreground/60 mt-1">{helpText}</p>}
      {maskedValue && onReplaceMaskedValue && (
        <button
          type="button"
          onClick={onReplaceMaskedValue}
          className="mt-2 text-xs font-medium text-foreground/70 transition-colors hover:text-foreground"
        >
          Replace API key
        </button>
      )}
    </div>
  )
}

function ModelsEditor({
  config,
  fieldError,
  isCustomProvider,
  onAddModel,
  onRemoveModel,
  onUpdateModel,
}: {
  config: ProviderConnectionForm
  fieldError?: string
  isCustomProvider: boolean
  onAddModel: () => void
  onRemoveModel: (id: string) => void
  onUpdateModel: (id: string, field: keyof ProviderModelDraft, value: string | boolean) => void
}) {
  return (
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
                autoComplete="off"
                onChange={(e) => onUpdateModel(model.id, "modelId", e.target.value)}
                className={inputClassName(Boolean(fieldError))}
              />
            </div>
            <div className="flex-1">
              <input
                type="text"
                placeholder="Display name"
                value={model.displayName}
                autoComplete="off"
                onChange={(e) => onUpdateModel(model.id, "displayName", e.target.value)}
                className={inputClassName(false)}
              />
            </div>
            {isCustomProvider && (
              <label className="flex items-center gap-2 text-xs text-foreground/70 pb-2">
                <input
                  type="checkbox"
                  checked={model.enabled}
                  onChange={(e) => onUpdateModel(model.id, "enabled", e.target.checked)}
                />
                Enabled
              </label>
            )}
            {config.models.length > 1 && (
              <button
                onClick={() => onRemoveModel(model.id)}
                className="p-2 hover:bg-[#efe9e4] rounded transition-colors"
              >
                <Trash2 className="w-4 h-4 text-foreground/70" />
              </button>
            )}
          </div>
        ))}
      </div>
      {fieldError && <p className="mt-2 text-xs text-destructive">{fieldError}</p>}
      <button
        onClick={onAddModel}
        className="mt-3 flex items-center gap-1 text-sm text-foreground/70 hover:text-foreground transition-colors"
      >
        <Plus className="w-4 h-4" />
        Add model
      </button>
    </div>
  )
}

function BuiltinModelPolicyEditor({
  provider,
  config,
  onPolicyChange,
  onPreferredModelChange,
  onToggleModel,
}: {
  provider: ProviderCatalogEntry | null
  config: ProviderConnectionForm
  onPolicyChange: (value: "all_catalog" | "restricted" | "custom_only") => void
  onPreferredModelChange: (value: string) => void
  onToggleModel: (modelId: string, enabled: boolean) => void
}) {
  const enabledIds = new Set(
    config.models.filter((model) => model.enabled).map((model) => model.modelId)
  )
  const availableModels = provider?.models ?? []

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium mb-2">Model policy</label>
        <select
          value={config.modelPolicy === "custom_only" ? "all_catalog" : config.modelPolicy}
          onChange={(e) => onPolicyChange(e.target.value as "all_catalog" | "restricted")}
          className="w-full px-3 py-2 border border-[#e8e4e0] rounded-lg text-sm"
        >
          <option value="all_catalog">All catalog models</option>
          <option value="restricted">Only selected catalog models</option>
        </select>
      </div>

      {config.modelPolicy === "restricted" && (
        <div>
          <label className="block text-sm font-medium mb-2">Enabled models</label>
          <div className="space-y-2 rounded-lg border border-[#efe9e4] p-3 max-h-56 overflow-y-auto">
            {availableModels.map((model) => (
              <label key={model.id} className="flex items-center justify-between gap-3 text-sm text-foreground/80">
                <span>{model.id}</span>
                <input
                  type="checkbox"
                  checked={enabledIds.has(model.id)}
                  onChange={(e) => onToggleModel(model.id, e.target.checked)}
                />
              </label>
            ))}
          </div>
        </div>
      )}

      <div>
        <label className="block text-sm font-medium mb-2">Preferred model</label>
        <select
          value={config.preferredModelId}
          onChange={(e) => onPreferredModelChange(e.target.value)}
          className="w-full px-3 py-2 border border-[#e8e4e0] rounded-lg text-sm"
        >
          <option value="">Select a preferred model</option>
          {(config.modelPolicy === "restricted"
            ? availableModels.filter((model) => enabledIds.has(model.id))
            : availableModels
          ).map((model) => (
            <option key={model.id} value={model.id}>
              {model.id}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
