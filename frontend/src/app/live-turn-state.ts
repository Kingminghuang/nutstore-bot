import type { SelectedModelRef } from "@/features/providers"
import type { LiveTurn, Session } from "@/features/session"
import type { TimelineEntry } from "@/shared/api/sidecar"

export type LiveTurnStateBySession = Record<string, LiveTurn>

export type PermissionRequestOutcome = {
  outcome:
    | {
        outcome: "selected"
        optionId: string
      }
    | {
        outcome: "cancelled"
      }
}

export function createEmptyLiveTurn(): LiveTurn {
  return {
    optimisticEntries: [],
    truncatedAfterSequence: null,
    assistantDraft: "",
    planEntries: [],
    toolCalls: [],
    waitingForPermission: false,
  }
}

export function updateLiveTurnBySession(
  prev: LiveTurnStateBySession,
  sessionId: string,
  updateLiveTurn: (liveTurn: LiveTurn) => LiveTurn | null
): LiveTurnStateBySession {
  const nextLiveTurn = updateLiveTurn(prev[sessionId] ?? createEmptyLiveTurn())
  if (nextLiveTurn == null) {
    const next = { ...prev }
    delete next[sessionId]
    return next
  }

  return {
    ...prev,
    [sessionId]: nextLiveTurn,
  }
}

export function createOptimisticUserEntry(sessionId: string, text: string): TimelineEntry {
  return {
    id: `live-user-${sessionId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    sessionId,
    runId: null,
    sequenceNo: Number.MAX_SAFE_INTEGER,
    entryKind: "user_input",
    displayRole: "user",
    stepId: null,
    stepNumber: null,
    contentText: text,
    createdAt: new Date().toISOString(),
  }
}

export function createLocalSession(
  sessionId: string,
  workspaceId: string,
  selectedModel: SelectedModelRef,
  text: string
): Session {
  const createdAt = new Date().toISOString()
  return {
    id: sessionId,
    workspaceId,
    title: "New session",
    titleSource: "placeholder",
    createdAt,
    updatedAt: createdAt,
    lastMessageAt: createdAt,
    messageCount: 1,
    lastMessagePreview: text,
    activeConnectionId: selectedModel.connectionId,
    activeModelId: selectedModel.modelId,
    timelineEntries: [],
    hasMoreHistory: false,
    nextBeforeSequence: null,
    isLoadingHistory: false,
    timelineHydrationStatus: "loaded",
  }
}

export function upsertSession(
  sessions: Session[],
  nextSession: Session,
  options?: { prepend?: boolean }
): Session[] {
  const exists = sessions.some((session) => session.id === nextSession.id)
  if (!exists) {
    return options?.prepend === false ? [...sessions, nextSession] : [nextSession, ...sessions]
  }

  return sessions.map((session) => (session.id === nextSession.id ? nextSession : session))
}

export function mergeTimelineEntriesWithLiveTurn(
  timelineEntries: TimelineEntry[],
  liveTurn: LiveTurn | null
): TimelineEntry[] {
  if (!liveTurn) {
    return timelineEntries
  }

  const persistedEntries =
    liveTurn.truncatedAfterSequence == null
      ? timelineEntries
      : timelineEntries.filter((entry) => entry.sequenceNo < liveTurn.truncatedAfterSequence)

  const optimisticEntries = liveTurn.optimisticEntries.filter((entry) => {
    return !persistedEntries.some(
      (persisted) =>
        persisted.entryKind === entry.entryKind &&
        persisted.displayRole === entry.displayRole &&
        persisted.contentText === entry.contentText
    )
  })

  return [...persistedEntries, ...optimisticEntries]
}

export function applyAssistantDraftChunk(currentDraft: string, incomingText: string): string {
  if (!incomingText) {
    return currentDraft
  }
  if (!currentDraft) {
    return incomingText
  }
  if (incomingText.startsWith(currentDraft)) {
    return incomingText
  }
  if (currentDraft.startsWith(incomingText)) {
    return currentDraft
  }
  return `${currentDraft}${incomingText}`
}