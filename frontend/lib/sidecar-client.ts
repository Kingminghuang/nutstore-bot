import {
  type ModelOptionsResponse,
  type ProviderCatalogResponse,
  type ProviderConnectionsResponse,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
} from "@/lib/provider-settings"
import { redactSensitive, redactText } from "@/lib/redaction"

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
    const payload = redactSensitive((await response.json()) as {
      detail?: string | Array<{ loc?: unknown; msg?: unknown; type?: unknown }>
    }) as {
      detail?: string | Array<{ loc?: unknown; msg?: unknown; type?: unknown }>
    }
    if (typeof payload.detail === "string" && payload.detail) {
      return redactText(payload.detail)
    }
    if (Array.isArray(payload.detail) && payload.detail.length > 0) {
      const detailMessage = payload.detail
        .map((item) => {
          const location = Array.isArray(item.loc)
            ? item.loc.map((part) => String(part)).join(".")
            : "body"
          const message = typeof item.msg === "string" && item.msg ? item.msg : "Validation failed"
          return `${location}: ${message}`
        })
        .join("; ")

      if (detailMessage) {
        return redactText(detailMessage)
      }
    }
  } catch {
    // Ignore non-JSON errors.
  }

  return `Request failed with status ${response.status}`
}
