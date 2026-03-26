import {
  type ModelOptionsResponse,
  type ProviderCatalogResponse,
  type ProviderConnectionsResponse,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
} from "@/lib/provider-settings"

export type RunStepUsage = {
  inputTokens: number
  outputTokens: number
  reasoningTokens: number
}

export type RunPlanningStep = {
  id: string
  runId: string
  sessionId: string
  sequenceNo: number
  stepId: string
  stepKind: "planning"
  stepNumber: null
  plan: string
  usage: RunStepUsage
  durationMs: number
  hasDelta: boolean
  createdAt: string
}

export type RunActionStep = {
  id: string
  runId: string
  sessionId: string
  sequenceNo: number
  stepId: string
  stepKind: "action"
  stepNumber: number
  codeAction: string | null
  actionOutput: unknown | null
  observations: string[]
  error: string | null
  usage: RunStepUsage
  durationMs: number
  hasDelta: boolean
  createdAt: string
}

export type RunHistoryStep = RunPlanningStep | RunActionStep

export type RunStepsResponse = {
  steps: RunHistoryStep[]
}

class SidecarClientError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "SidecarClientError"
    this.status = status
  }
}

export async function getProviderCatalog(): Promise<ProviderCatalogResponse> {
  return sidecarFetch<ProviderCatalogResponse>("/provider-catalog")
}

export async function getProviders(): Promise<ProviderConnectionsResponse> {
  return sidecarFetch<ProviderConnectionsResponse>("/providers")
}

export async function getModelOptions(): Promise<ModelOptionsResponse> {
  return sidecarFetch<ModelOptionsResponse>("/model-options")
}

export async function createProvider(
  payload: SaveProviderPayload
): Promise<ProviderConnectionDetail> {
  return sidecarFetch<ProviderConnectionDetail>("/providers", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export async function updateProvider(
  providerId: string,
  payload: Partial<SaveProviderPayload>
): Promise<ProviderConnectionDetail> {
  return sidecarFetch<ProviderConnectionDetail>(`/providers/${providerId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  })
}

export async function deleteProvider(providerId: string): Promise<void> {
  await sidecarFetch<void>(`/providers/${providerId}`, { method: "DELETE" })
}

export async function getRunSteps(runId: string): Promise<RunStepsResponse> {
  return sidecarFetch<RunStepsResponse>(`/runs/${runId}/steps`)
}

async function sidecarFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/sidecar/proxy?path=${encodeURIComponent(path)}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  })

  if (!response.ok) {
    const message = await readErrorMessage(response)
    throw new SidecarClientError(message, response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string }
    if (payload.detail) {
      return payload.detail
    }
  } catch {
    // Ignore non-JSON errors.
  }

  return `Request failed with status ${response.status}`
}
