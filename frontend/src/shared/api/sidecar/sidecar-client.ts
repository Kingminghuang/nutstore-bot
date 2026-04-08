import {
  type ModelOptionsResponse,
  type ProviderCatalogResponse,
  type ProviderConnectionsResponse,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
} from "@/features/providers"
import { redactSensitive, redactText } from "@/shared/lib"

export type TimelineEntryUsage = {
  inputTokens: number
  outputTokens: number
  reasoningTokens: number
}

export type TimelineEntryBase = {
  id: string
  sessionId: string
  runId: string | null
  sequenceNo: number
  entryKind: "user_input" | "planning" | "action" | "final_answer" | "system_notice"
  displayRole: "user" | "assistant" | "system"
  stepId: string | null
  stepNumber: number | null
  contentText: string | null
  createdAt: string
}

export type UserInputEntry = TimelineEntryBase & {
  entryKind: "user_input"
  displayRole: "user"
}

export type PlanningEntry = TimelineEntryBase & {
  entryKind: "planning"
  displayRole: "assistant"
}

export type ActionEntry = TimelineEntryBase & {
  entryKind: "action"
  displayRole: "assistant"
  contentJson: {
    thought?: string | null
    toolCalls: Array<{
      id?: string | null
      name: string
      argumentsText: string
    }>
    observations: string[]
    codeAction: string | null
    actionOutput: unknown | null
    error: string | null
    usage: TimelineEntryUsage
    durationMs: number
  } | null
}

export type FinalAnswerEntry = TimelineEntryBase & {
  entryKind: "final_answer"
  displayRole: "assistant"
}

export type SystemNoticeEntry = TimelineEntryBase & {
  entryKind: "system_notice"
  displayRole: "system"
  contentJson: {
    noticeCode?: "failed" | "cancelled" | "info"
  } | null
}

export type TimelineEntry =
  | UserInputEntry
  | PlanningEntry
  | ActionEntry
  | FinalAnswerEntry
  | SystemNoticeEntry

export type TimelineResponse = {
  entries: TimelineEntry[]
  pagination?: {
    hasMore?: boolean
    nextBeforeSequence?: number | null
  }
}

class NSBotClientError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "NSBotClientError"
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

export async function validateProvider(
  providerId: string,
  payload?: { modelId?: string }
): Promise<{
  ok: boolean
  providerId: string
  modelId: string
  runtimeProvider?: string
  baseUrl?: string | null
  errorCode?: string
  errorMessage?: string
  healthStatus: string
  healthMessage: string | null
  lastValidatedAt: string | null
}> {
  return sidecarFetch(`/providers/${providerId}/validate`, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  })
}

export async function deleteProvider(providerId: string): Promise<void> {
  await sidecarFetch<void>(`/providers/${providerId}`, { method: "DELETE" })
}

export async function getSessionTimeline(
  sessionId: string,
  options?: { limit?: number; beforeSequence?: number | null }
): Promise<TimelineResponse> {
  const params = new URLSearchParams()
  if (options?.limit != null) {
    params.set("limit", String(options.limit))
  }
  if (options?.beforeSequence != null) {
    params.set("beforeSequence", String(options.beforeSequence))
  }
  const query = params.size > 0 ? `?${params.toString()}` : ""
  return sidecarFetch<TimelineResponse>(`/sessions/${sessionId}/timeline${query}`)
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
    throw new NSBotClientError(message, response.status)
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
