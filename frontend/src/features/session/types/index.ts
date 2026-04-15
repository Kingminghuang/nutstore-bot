import type { ConversationEvent, TimelineEvent } from "@/shared/api/sidecar"

export type Session = {
  id: string
  workspaceId: string
  title: string
  titleSource: "placeholder" | "heuristic" | "model" | "manual"
  createdAt: string
  updatedAt: string
  lastMessageAt: string | null
  messageCount: number
  lastMessagePreview: string | null
  activeConnectionId: string | null
  activeModelId: string | null
  timelineEvents: TimelineEvent[]
  hasMoreHistory: boolean
  nextBeforeSequence: number | null
  isLoadingHistory: boolean
  timelineHydrationStatus: "idle" | "loading" | "loaded"
}

export type PermissionMode = "auto_allow" | "ask"

export type LivePlanEntry = {
  id: string
  content: string
  priority: "high" | "medium" | "low" | null
  status: "pending" | "completed" | "failed"
}

export type LiveToolCall = {
  toolCallId: string
  title: string
  kind: string
  status: "pending" | "in_progress" | "completed" | "failed" | "cancelled"
  rawInput?: unknown
  content?: Array<{
    type: "content" | "diff"
    content?: {
      type: "text"
      text: string
    }
    path?: string
    oldText?: string | null
    newText?: string
  }>
}

export type AvailableCommand = {
  name: string
  description: string
  hint?: string
}

export type LiveTurn = {
  optimisticEvents: ConversationEvent[]
  truncatedAfterSequence: number | null
  assistantDraft: string
  thinkingDraft: string
  planEntries: LivePlanEntry[]
  toolCalls: LiveToolCall[]
  availableCommands: AvailableCommand[]
  waitingForPermission: boolean
}

export type PendingPermissionRequest = {
  sessionId: string
  toolCallId: string
  title: string
  kind: string
  options: Array<{
    optionId: string
    name: string
    kind: string
  }>
}

export type ComposerAttachment = {
  id: string
  sessionId: string
  workspaceId: string
  fileName: string
  mimeType: string
  sizeBytes: number
  status: "uploaded" | "consumed" | "deleted" | "missing"
  createdAt: string
  updatedAt: string
}

export type DraftAttachment = {
  id: string
  workspaceId: string
  fileName: string
  mimeType: string
  sizeBytes: number
  createdAt: string
  updatedAt: string
}

export type Project = {
  id: string
  name: string
  path: string
  sessions: Session[]
}

export type WorkspaceSummary = {
  id: string
  name: string
  pathLabel: string
  realPath: string
  createdAt: string
  updatedAt: string
}
