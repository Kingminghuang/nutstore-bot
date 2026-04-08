import type { TimelineEntry } from "@/shared/api/sidecar"

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
  timelineEntries: TimelineEntry[]
  hasMoreHistory: boolean
  nextBeforeSequence: number | null
  isLoadingHistory: boolean
  timelineHydrationStatus: "idle" | "loading" | "loaded"
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
