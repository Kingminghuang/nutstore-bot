import {
  type ModelOptionsResponse,
  type ProviderCatalogResponse,
  type ProviderConnectionsResponse,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
} from "@/features/providers"
import { acpClient } from "./acp-client"

export type UserDisplayBlock =
  | {
      type: "text"
      text: string
    }
  | {
      type: "resource"
      label: string
      uri: string
    }

export type ConversationEventUsage = {
  inputTokens: number
  outputTokens: number
  reasoningTokens: number
}

export type TimelineEvent = {
  eventId: string
  sessionId: string
  turnId: string | null
  sequenceNo: number
  eventType: string
  payload: {
    params?: {
      update?: Record<string, unknown>
    }
  } | null
  createdAt: string
}

export type ConversationEventBase = {
  id: string
  eventId?: string
  sessionId: string
  turnId: string | null
  sequenceNo: number
  entryKind: "user_input" | "planning" | "action" | "final_answer" | "thinking" | "system_notice"
  displayRole: "user" | "assistant" | "system"
  stepId: string | null
  stepNumber: number | null
  contentText: string | null
  editableText?: string | null
  displayBlocks?: UserDisplayBlock[]
  promptBlocks?: Array<Record<string, unknown>>
  createdAt: string
}

export type UserInputEntry = ConversationEventBase & {
  entryKind: "user_input"
  displayRole: "user"
}

export type PlanningEntry = ConversationEventBase & {
  entryKind: "planning"
  displayRole: "assistant"
}

export type ActionEntry = ConversationEventBase & {
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
    usage: ConversationEventUsage
    durationMs: number
  } | null
}

export type FinalAnswerEntry = ConversationEventBase & {
  entryKind: "final_answer"
  displayRole: "assistant"
}

export type SystemNoticeEntry = ConversationEventBase & {
  entryKind: "system_notice"
  displayRole: "system"
  contentJson: {
    noticeCode?: "failed" | "cancelled" | "info"
  } | null
}

export type ThinkingEntry = ConversationEventBase & {
  entryKind: "thinking"
  displayRole: "assistant"
}

export type ConversationEvent =
  | UserInputEntry
  | PlanningEntry
  | ActionEntry
  | FinalAnswerEntry
  | ThinkingEntry
  | SystemNoticeEntry

export type TimelineResponse = {
  events: TimelineEvent[]
  pagination?: {
    hasMore?: boolean
    nextBeforeSequence?: number | null
  }
}

export type WorkspaceEntrySearchResult = {
  name: string
  relativePath: string
  parentPath: string
  absolutePath: string
  uri: string
  entryType: "file" | "directory"
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function buildUserDisplayBlocks(promptBlocks: unknown, fallbackText: string): UserDisplayBlock[] | undefined {
  if (!Array.isArray(promptBlocks)) {
    return fallbackText.trim() ? [{ type: "text", text: fallbackText }] : undefined
  }

  const blocks: UserDisplayBlock[] = []
  for (const block of promptBlocks) {
    if (!block || typeof block !== "object") {
      continue
    }
    const normalized = block as Record<string, unknown>
    const blockType = asString(normalized.type)
    if (blockType === "text") {
      const text = asString(normalized.text)
      if (text) {
        blocks.push({ type: "text", text })
      }
      continue
    }
    if (blockType === "resource") {
      const resource = normalized.resource
      if (!resource || typeof resource !== "object") {
        continue
      }
      const resourceRecord = resource as Record<string, unknown>
      const uri = asString(resourceRecord.uri)
      if (!uri) {
        continue
      }
      blocks.push({
        type: "resource",
        label: asString(resourceRecord.title) ?? uri.split("/").filter(Boolean).pop() ?? "resource",
        uri,
      })
      continue
    }
    if (blockType === "resource_link") {
      const uri = asString(normalized.uri)
      if (!uri) {
        continue
      }
      blocks.push({
        type: "resource",
        label:
          asString(normalized.title) ??
          asString(normalized.name) ??
          uri.split("/").filter(Boolean).pop() ??
          "resource",
        uri,
      })
    }
  }

  return blocks.length > 0 ? blocks : fallbackText.trim() ? [{ type: "text", text: fallbackText }] : undefined
}

export function projectConversationEvents(sessionId: string, events: TimelineEvent[]): ConversationEvent[] {
  const normalized: ConversationEvent[] = []
  const actionIndexByToolCallId = new Map<string, number>()
  let fallbackSequence = 0

  for (const event of events) {
    fallbackSequence += 1
    const eventId = asString(event.eventId) ?? `evt_${sessionId}_${fallbackSequence}`
    const sequenceNo = asNumber(event.sequenceNo) ?? fallbackSequence
    const createdAt = asString(event.createdAt) ?? new Date(0).toISOString()
    const update = event.payload?.params?.update
    if (!update || typeof update !== "object") {
      continue
    }
    const sessionUpdate = asString((update as { sessionUpdate?: unknown }).sessionUpdate)
    if (!sessionUpdate) {
      continue
    }

    if (sessionUpdate === "user_message_chunk" || sessionUpdate === "agent_message_chunk") {
      const content =
        update && typeof update === "object"
          ? (update as { content?: unknown }).content
          : undefined
      const displayText =
        content && typeof content === "object"
          ? asString((content as { displayText?: unknown }).displayText)
          : null
      const editableText =
        content && typeof content === "object"
          ? asString((content as { editableText?: unknown }).editableText)
          : null
      const promptBlocks =
        content && typeof content === "object"
          ? (content as { promptBlocks?: unknown }).promptBlocks
          : undefined
      const text =
        content && typeof content === "object"
          ? asString((content as { text?: unknown }).text)
          : null
      const projectedText = sessionUpdate === "user_message_chunk" ? displayText ?? text : text
      if (!projectedText) {
        continue
      }
      if (sessionUpdate === "user_message_chunk") {
        normalized.push({
          id: eventId,
          eventId,
          sessionId,
          turnId: event.turnId,
          sequenceNo,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: projectedText,
          editableText: editableText ?? projectedText,
          displayBlocks: buildUserDisplayBlocks(promptBlocks, projectedText),
          promptBlocks: Array.isArray(promptBlocks)
            ? promptBlocks.filter(
                (block): block is Record<string, unknown> => !!block && typeof block === "object"
              )
            : undefined,
          createdAt,
        })
      } else {
        normalized.push({
          id: eventId,
          eventId,
          sessionId,
          turnId: event.turnId,
          sequenceNo,
          entryKind: "final_answer",
          displayRole: "assistant",
          stepId: null,
          stepNumber: null,
          contentText: projectedText,
          createdAt,
        })
      }
      continue
    }

    if (sessionUpdate === "agent_thought_chunk") {
      const content = (update as { content?: unknown }).content
      const text =
        content && typeof content === "object"
          ? asString((content as { text?: unknown }).text)
          : null
      if (!text) {
        continue
      }
      const previous = normalized[normalized.length - 1]
      if (previous?.entryKind === "thinking") {
        previous.contentText = `${previous.contentText ?? ""}${text}`
        continue
      }
      normalized.push({
        id: eventId,
        eventId,
        sessionId,
        turnId: event.turnId,
        sequenceNo,
        entryKind: "thinking",
        displayRole: "assistant",
        stepId: null,
        stepNumber: null,
        contentText: text,
        createdAt,
      })
      continue
    }

    if (sessionUpdate === "plan") {
      const entries =
        update && typeof update === "object" ? (update as { entries?: unknown }).entries : null
      if (!Array.isArray(entries)) {
        continue
      }
      entries.forEach((entry, idx) => {
        if (!entry || typeof entry !== "object") {
          return
        }
        const content = asString((entry as { content?: unknown }).content)
        if (!content) {
          return
        }
        normalized.push({
          id: `${eventId}:plan:${idx}`,
          sessionId,
          turnId: event.turnId,
          sequenceNo,
          entryKind: "planning",
          displayRole: "assistant",
          stepId: null,
          stepNumber: null,
          contentText: content,
          createdAt,
        })
      })
      continue
    }

    if (sessionUpdate === "tool_call") {
      const toolCallId = asString((update as { toolCallId?: unknown }).toolCallId) ?? eventId
      const title = asString((update as { title?: unknown }).title) ?? "Tool call"
      const rawInput = (update as { rawInput?: unknown }).rawInput
      const rawInputText =
        typeof rawInput === "string"
          ? rawInput
          : rawInput == null
            ? null
            : JSON.stringify(rawInput)
      const entry: ActionEntry = {
        id: `${eventId}:action`,
        sessionId,
        turnId: event.turnId,
        sequenceNo,
        entryKind: "action",
        displayRole: "assistant",
        stepId: null,
        stepNumber: null,
        contentText: null,
        createdAt,
        contentJson: {
          thought: null,
          toolCalls: [
            {
              id: toolCallId,
              name: title,
              argumentsText: rawInputText ?? "",
            },
          ],
          observations: [],
          codeAction: null,
          actionOutput: null,
          error: null,
          usage: {
            inputTokens: 0,
            outputTokens: 0,
            reasoningTokens: 0,
          },
          durationMs: 0,
        },
      }
      actionIndexByToolCallId.set(toolCallId, normalized.length)
      normalized.push(entry)
      continue
    }

    if (sessionUpdate === "tool_call_update") {
      const toolCallId = asString((update as { toolCallId?: unknown }).toolCallId)
      if (!toolCallId) {
        continue
      }
      const content = (update as { content?: unknown }).content
      const observations: string[] = []
      if (Array.isArray(content)) {
        for (const item of content) {
          if (!item || typeof item !== "object") {
            continue
          }
          const payload = (item as { content?: unknown }).content
          const text =
            payload && typeof payload === "object"
              ? asString((payload as { text?: unknown }).text)
              : null
          if (text) {
            observations.push(text)
          }
        }
      }
      const existingIndex = actionIndexByToolCallId.get(toolCallId)
      if (existingIndex != null) {
        const existing = normalized[existingIndex]
        if (existing.entryKind === "action" && existing.contentJson) {
          existing.contentJson.observations = [...existing.contentJson.observations, ...observations]
        }
        continue
      }
      normalized.push({
        id: `${eventId}:action-update`,
        sessionId,
        turnId: event.turnId,
        sequenceNo,
        entryKind: "action",
        displayRole: "assistant",
        stepId: null,
        stepNumber: null,
        contentText: null,
        createdAt,
        contentJson: {
          thought: null,
          toolCalls: [{ id: toolCallId, name: "Tool call", argumentsText: "" }],
          observations,
          codeAction: null,
          actionOutput: null,
          error: null,
          usage: {
            inputTokens: 0,
            outputTokens: 0,
            reasoningTokens: 0,
          },
          durationMs: 0,
        },
      })
    }
  }

  return normalized
}

export async function getProviderCatalog(): Promise<ProviderCatalogResponse> {
  return acpClient.request<ProviderCatalogResponse>("_nsbot/provider/catalog")
}

export async function getProviders(): Promise<ProviderConnectionsResponse> {
  return acpClient.request<ProviderConnectionsResponse>("_nsbot/provider/list")
}

export async function getModelOptions(): Promise<ModelOptionsResponse> {
  return acpClient.request<ModelOptionsResponse>("_nsbot/provider/model_options")
}

export async function createProvider(
  payload: SaveProviderPayload
): Promise<ProviderConnectionDetail> {
  return acpClient.request<ProviderConnectionDetail>(
    "_nsbot/provider/create",
    payload as Record<string, unknown>
  )
}

export async function updateProvider(
  providerId: string,
  payload: Partial<SaveProviderPayload>
): Promise<ProviderConnectionDetail> {
  return acpClient.request<ProviderConnectionDetail>("_nsbot/provider/update", {
    providerId,
    ...(payload as Record<string, unknown>),
  })
}

export async function deleteProvider(providerId: string): Promise<void> {
  await acpClient.request("_nsbot/provider/delete", { providerId })
}

export async function getSessionTimeline(
  sessionId: string,
  options?: { limit?: number; beforeSequence?: number | null }
): Promise<TimelineResponse> {
  const response = await acpClient.request<{
    events?: TimelineEvent[]
    pagination?: {
      hasMore?: boolean
      nextBeforeSequence?: number | null
    }
  }>("_nsbot/timeline/list", {
    sessionId,
    limit: options?.limit,
    beforeSequence: options?.beforeSequence ?? null,
  })

  return {
    events: Array.isArray(response.events) ? response.events : [],
    pagination: response.pagination,
  }
}

export async function loadSession(
  sessionId: string,
  cwd: string
): Promise<{ configOptions: Array<Record<string, unknown>> }> {
  return acpClient.request("session/load", { sessionId, cwd, mcpServers: [] })
}

export async function listWorkspaces(): Promise<{ workspaces: Array<Record<string, unknown>> }> {
  return acpClient.request("_nsbot/workspace/list")
}

export async function createWorkspace(payload: Record<string, unknown>) {
  return acpClient.request("_nsbot/workspace/create", payload)
}

export async function updateWorkspace(workspaceId: string, payload: Record<string, unknown>) {
  return acpClient.request("_nsbot/workspace/update", { workspaceId, ...payload })
}

export async function deleteWorkspace(workspaceId: string): Promise<void> {
  await acpClient.request("_nsbot/workspace/delete", { workspaceId })
}

export async function listWorkspaceSessions(workspaceId: string): Promise<{ sessions: Array<Record<string, unknown>> }> {
  return acpClient.request("_nsbot/workspace/sessions/list", { workspaceId })
}

export async function workspaceSidecarIndexStatus(workspaceId: string) {
  return acpClient.request("_nsbot/workspace/sidecar_index/status", { workspaceId })
}

export async function searchWorkspaceEntries(
  workspaceId: string,
  query: string,
  options?: { limit?: number }
): Promise<{ entries: WorkspaceEntrySearchResult[] }> {
  const response = await acpClient.request<{ entries?: WorkspaceEntrySearchResult[] }>(
    "_nsbot/workspace/find_entries",
    {
      workspaceId,
      query,
      limit: options?.limit,
    }
  )
  return {
    entries: Array.isArray(response.entries) ? response.entries : [],
  }
}

export async function listAttachments(sessionId: string): Promise<{ attachments: Array<Record<string, unknown>> }> {
  return acpClient.request("_nsbot/attachment/list", { sessionId })
}

export async function createAttachment(sessionId: string, payload: Record<string, unknown>) {
  return acpClient.request("_nsbot/attachment/create", { sessionId, ...payload })
}

export async function deleteAttachment(sessionId: string, attachmentId: string): Promise<void> {
  await acpClient.request("_nsbot/attachment/delete", { sessionId, attachmentId })
}

export async function listDraftAttachments(workspaceId: string): Promise<{ draftAttachments: Array<Record<string, unknown>> }> {
  return acpClient.request("_nsbot/draft_attachment/list", { workspaceId })
}

export async function createDraftAttachment(workspaceId: string, payload: Record<string, unknown>) {
  return acpClient.request("_nsbot/draft_attachment/create", { workspaceId, ...payload })
}

export async function deleteDraftAttachment(
  workspaceId: string,
  draftAttachmentId: string
): Promise<void> {
  await acpClient.request("_nsbot/draft_attachment/delete", { workspaceId, draftAttachmentId })
}

export async function updateSessionMeta(sessionId: string, payload: Record<string, unknown>) {
  return acpClient.request("_nsbot/session/update_meta", { sessionId, ...payload })
}

export async function deleteSession(sessionId: string): Promise<void> {
  await acpClient.request("_nsbot/session/delete", { sessionId })
}
