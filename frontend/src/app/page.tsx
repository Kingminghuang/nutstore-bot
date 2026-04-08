"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { MainContent } from "@/features/runs"
import { SettingsModal } from "@/features/settings"
import { Sidebar } from "@/features/workspaces"
import {
  createProvider,
  deleteProvider,
  getModelOptions,
  getProviderCatalog,
  getProviders,
  getSessionTimeline,
  updateProvider,
  validateProvider,
  type TimelineEntry,
} from "@/shared/api/sidecar"
import {
  normalizeSelectedReasoningEffort,
  type ModelOptionGroup,
  type ProviderCatalogEntry,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
  type SelectedReasoningEffort,
  type SelectedModelRef,
  isSelectedModelAvailable,
} from "@/features/providers"
import { parseRunEventEnvelope, type RunStreamEvent } from "@/features/runs"
import type { ComposerAttachment, DraftAttachment, Project, Session, WorkspaceSummary } from "@/features/session"

type TimelinePageResponse = {
  entries: TimelineEntry[]
  pagination?: {
    hasMore?: boolean
    nextBeforeSequence?: number | null
  }
}

type ServerSession = Omit<
  Session,
  "timelineEntries" | "hasMoreHistory" | "nextBeforeSequence" | "isLoadingHistory" | "timelineHydrationStatus"
>

type WorkspaceSidecarIndexStatus = {
  workspaceId: string
  status: "indexed" | "not_started" | "disabled"
  lastIndexedAt: string | null
  stats: {
    scanned: number
    converted: number
    skipped: number
    failed: number
  }
  sourceCount: number
}

const SIDEBAR_MIN = 160
const SIDEBAR_MAX = 480
const SIDEBAR_DEFAULT = 230
const TIMELINE_PAGE_SIZE = 50
const WORKSPACE_INDEX_POLL_INTERVAL_MS = 2000
const WORKSPACE_INDEX_POLL_MAX_ATTEMPTS = 30

function withSessionHistoryDefaults(session: ServerSession): Session {
  return {
    ...session,
    timelineEntries: [],
    hasMoreHistory: false,
    nextBeforeSequence: null,
    isLoadingHistory: false,
    timelineHydrationStatus: "idle",
  }
}

function mergeSessionWithLocalHistory(
  incoming: ServerSession,
  existing: Session | undefined,
  timelineEntries: TimelineEntry[]
): Session {
  return {
    ...incoming,
    timelineEntries,
    hasMoreHistory: existing?.hasMoreHistory ?? false,
    nextBeforeSequence: existing?.nextBeforeSequence ?? null,
    isLoadingHistory: existing?.isLoadingHistory ?? false,
    timelineHydrationStatus:
      timelineEntries.length > 0 ? "loaded" : (existing?.timelineHydrationStatus ?? "loaded"),
  }
}

function updateSessionInWorkspace(
  prev: Record<string, Session[]>,
  workspaceId: string,
  sessionId: string,
  updateSession: (session: Session) => Session
): Record<string, Session[]> {
  return {
    ...prev,
    [workspaceId]: (prev[workspaceId] ?? []).map((session) =>
      session.id === sessionId ? updateSession(session) : session
    ),
  }
}

function appendTimelineEntry(entries: TimelineEntry[], entry: TimelineEntry): TimelineEntry[] {
  const existingIndex = entries.findIndex((candidate) => candidate.id === entry.id)
  if (existingIndex >= 0) {
    const next = [...entries]
    next[existingIndex] = entry
    return next
  }
  return [...entries, entry]
}

export default function Home() {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [sessionsByWorkspace, setSessionsByWorkspace] = useState<Record<string, Session[]>>({})
  const [activeDraftWorkspaceId, setActiveDraftWorkspaceId] = useState<string | null>(null)
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogEntry[]>([])
  const [providerConnections, setProviderConnections] = useState<ProviderConnectionDetail[]>([])
  const [modelOptionGroups, setModelOptionGroups] = useState<ModelOptionGroup[]>([])
  const [selectedModel, setSelectedModel] = useState<SelectedModelRef | null>(null)
  const [selectedReasoningEffort, setSelectedReasoningEffort] =
    useState<SelectedReasoningEffort>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT)
  const [isLoadingProviders, setIsLoadingProviders] = useState(true)
  const [providerError, setProviderError] = useState<string | null>(null)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [sessionRunStatusById, setSessionRunStatusById] = useState<Record<string, boolean>>({})
  const [sessionActiveRunIdById, setSessionActiveRunIdById] = useState<Record<string, string | null>>({})
  const [attachmentsBySession, setAttachmentsBySession] = useState<Record<string, ComposerAttachment[]>>({})
  const [draftAttachmentsByWorkspace, setDraftAttachmentsByWorkspace] = useState<Record<string, DraftAttachment[]>>({})
  const [uploadingAttachmentTargetId, setUploadingAttachmentTargetId] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const sessionActiveRunIdByIdRef = useRef<Record<string, string | null>>({})
  const lastHydrationAttemptSessionIdRef = useRef<string | null>(null)
  const isDragging = useRef(false)

  const refreshProviderState = useCallback(async () => {
    setIsLoadingProviders(true)
    setProviderError(null)

    try {
      const [catalogResponse, providersResponse, modelOptionsResponse] = await Promise.all([
        getProviderCatalog(),
        getProviders(),
        getModelOptions(),
      ])

      setProviderCatalog(catalogResponse.providers)
      setProviderConnections(providersResponse.connections)
      setModelOptionGroups(modelOptionsResponse.groups)
      setSelectedModel((current) => {
        if (isSelectedModelAvailable(current, modelOptionsResponse.groups)) {
          return current
        }
        return modelOptionsResponse.defaultSelection
      })
    } catch (error) {
      setProviderError(error instanceof Error ? error.message : "Failed to load provider data")
      setProviderCatalog([])
      setProviderConnections([])
      setModelOptionGroups([])
      setSelectedModel(null)
      setSelectedReasoningEffort(null)
    } finally {
      setIsLoadingProviders(false)
    }
  }, [])

  const refreshWorkspaceState = useCallback(async () => {
    setWorkspaceError(null)
    try {
      const workspacesResponse = await sidecarFetch<{ workspaces: WorkspaceSummary[] }>("/workspaces")
      const nextWorkspaces = workspacesResponse.workspaces
      setWorkspaces(nextWorkspaces)

      const sessionsEntries = await Promise.all(
        nextWorkspaces.map(async (workspace) => {
          const sessionsResponse = await sidecarFetch<{ sessions: ServerSession[] }>(
            `/workspaces/${workspace.id}/sessions`
          )
          return [
            workspace.id,
            sessionsResponse.sessions.map((session) => withSessionHistoryDefaults(session)),
          ] as const
        })
      )

      const nextSessionsByWorkspace = Object.fromEntries(sessionsEntries)
      setSessionsByWorkspace(nextSessionsByWorkspace)
      setDraftAttachmentsByWorkspace((prev) => {
        const next: Record<string, DraftAttachment[]> = {}
        for (const workspace of nextWorkspaces) {
          if (prev[workspace.id]) {
            next[workspace.id] = prev[workspace.id]
          }
        }
        return next
      })

      setActiveWorkspaceId((current) => current ?? nextWorkspaces[0]?.id ?? null)
      setActiveSessionId((current) => {
        if (!current) {
          return nextSessionsByWorkspace[nextWorkspaces[0]?.id ?? ""]?.[0]?.id ?? null
        }
        for (const sessions of Object.values(nextSessionsByWorkspace)) {
          if (sessions.some((session) => session.id === current)) {
            return current
          }
        }
        return nextSessionsByWorkspace[nextWorkspaces[0]?.id ?? ""]?.[0]?.id ?? null
      })
      setActiveDraftWorkspaceId((current) => {
        if (!current) {
          return null
        }
        return nextWorkspaces.some((workspace) => workspace.id === current) ? current : null
      })
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "Failed to load workspaces")
      setWorkspaces([])
      setSessionsByWorkspace({})
      setAttachmentsBySession({})
      setDraftAttachmentsByWorkspace({})
      setActiveDraftWorkspaceId(null)
      setActiveWorkspaceId(null)
      setActiveSessionId(null)
    }
  }, [])

  useEffect(() => {
    void Promise.all([refreshProviderState(), refreshWorkspaceState()])
  }, [refreshProviderState, refreshWorkspaceState])

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
    }
  }, [])

  useEffect(() => {
    sessionActiveRunIdByIdRef.current = sessionActiveRunIdById
  }, [sessionActiveRunIdById])

  useEffect(() => {
    setSelectedReasoningEffort((current) =>
      normalizeSelectedReasoningEffort(selectedModel, modelOptionGroups, current)
    )
  }, [modelOptionGroups, selectedModel])

  const activeSession = useMemo(
    () =>
      activeWorkspaceId == null || activeSessionId == null
        ? null
        : sessionsByWorkspace[activeWorkspaceId]?.find((session) => session.id === activeSessionId) ??
          null,
    [activeSessionId, activeWorkspaceId, sessionsByWorkspace]
  )
  const activeSessionRunId = activeSessionId ? (sessionActiveRunIdById[activeSessionId] ?? null) : null
  const isActiveSessionRunning = activeSessionId ? Boolean(sessionRunStatusById[activeSessionId]) : false

  const isDraftSessionActive =
    activeWorkspaceId != null &&
    activeSessionId == null &&
    activeDraftWorkspaceId === activeWorkspaceId

  const activeSessionHydrationStatus = activeSession?.timelineHydrationStatus ?? null

  useEffect(() => {
    lastHydrationAttemptSessionIdRef.current = null
  }, [activeSessionId])

  useEffect(() => {
    if (!activeSessionId || !activeWorkspaceId) {
      return
    }
    if (activeSessionHydrationStatus !== "idle") {
      return
    }
    if (lastHydrationAttemptSessionIdRef.current === activeSessionId) {
      return
    }

    lastHydrationAttemptSessionIdRef.current = activeSessionId

    setSessionsByWorkspace((prev) =>
      updateSessionInWorkspace(prev, activeWorkspaceId, activeSessionId, (session) =>
        session.timelineHydrationStatus === "idle"
          ? {
              ...session,
              timelineHydrationStatus: "loading",
            }
          : session
      )
    )

    void getSessionTimeline(activeSessionId, { limit: TIMELINE_PAGE_SIZE })
      .then((response) => {
        setSessionsByWorkspace((prev) => ({
          ...prev,
          [activeWorkspaceId]: (prev[activeWorkspaceId] ?? []).map((session) =>
            session.id === activeSessionId
              ? {
                  ...session,
                  timelineEntries: response.entries,
                  hasMoreHistory: response.pagination?.hasMore ?? false,
                  nextBeforeSequence: response.pagination?.nextBeforeSequence ?? null,
                  isLoadingHistory: false,
                  timelineHydrationStatus: "loaded",
                }
              : session
          ),
        }))
      })
      .catch(() => {
        setSessionsByWorkspace((prev) =>
          updateSessionInWorkspace(prev, activeWorkspaceId, activeSessionId, (session) => ({
            ...session,
            timelineHydrationStatus: "idle",
          }))
        )
      })
  }, [activeSessionHydrationStatus, activeSessionId, activeWorkspaceId])

  useEffect(() => {
    if (!activeSessionId) {
      return
    }
    if (attachmentsBySession[activeSessionId] != null) {
      return
    }

    let cancelled = false
    void sidecarFetch<{ attachments: ComposerAttachment[] }>(`/sessions/${activeSessionId}/attachments`)
      .then((response) => {
        if (cancelled) {
          return
        }
        setAttachmentsBySession((prev) => ({
          ...prev,
          [activeSessionId]: response.attachments,
        }))
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [activeSessionId, attachmentsBySession])

  useEffect(() => {
    if (!isDraftSessionActive || !activeWorkspaceId) {
      return
    }
    if (draftAttachmentsByWorkspace[activeWorkspaceId] != null) {
      return
    }

    let cancelled = false
    void sidecarFetch<{ draftAttachments: DraftAttachment[] }>(
      `/workspaces/${activeWorkspaceId}/draft-attachments`
    )
      .then((response) => {
        if (cancelled) {
          return
        }
        setDraftAttachmentsByWorkspace((prev) => ({
          ...prev,
          [activeWorkspaceId]: response.draftAttachments,
        }))
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [activeWorkspaceId, draftAttachmentsByWorkspace, isDraftSessionActive])

  const handleLoadEarlierTimeline = useCallback(async () => {
    if (!activeSessionId || !activeWorkspaceId || !activeSession) {
      return
    }
    if (!activeSession.hasMoreHistory || activeSession.nextBeforeSequence == null) {
      return
    }

    setSessionsByWorkspace((prev) =>
      updateSessionInWorkspace(prev, activeWorkspaceId, activeSessionId, (session) => ({
        ...session,
        isLoadingHistory: true,
      }))
    )

    try {
      const response = await getSessionTimeline(activeSessionId, {
        limit: TIMELINE_PAGE_SIZE,
        beforeSequence: activeSession.nextBeforeSequence,
      })

      setSessionsByWorkspace((prev) =>
        updateSessionInWorkspace(prev, activeWorkspaceId, activeSessionId, (session) => {
          const existingIds = new Set(session.timelineEntries.map((entry) => entry.id))
          const olderEntries = response.entries.filter((entry) => !existingIds.has(entry.id))
          return {
            ...session,
            timelineEntries: [...olderEntries, ...session.timelineEntries],
            hasMoreHistory: response.pagination?.hasMore ?? false,
            nextBeforeSequence: response.pagination?.nextBeforeSequence ?? null,
            isLoadingHistory: false,
          }
        })
      )
    } catch {
      setSessionsByWorkspace((prev) =>
        updateSessionInWorkspace(prev, activeWorkspaceId, activeSessionId, (session) => ({
          ...session,
          isLoadingHistory: false,
        }))
      )
    }
  }, [activeSession, activeSessionId, activeWorkspaceId])

  const handleSidebarResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      isDragging.current = true
      const startX = e.clientX
      const startWidth = sidebarWidth

      const onMove = (moveEvent: MouseEvent) => {
        if (!isDragging.current) return
        const next = Math.min(
          SIDEBAR_MAX,
          Math.max(SIDEBAR_MIN, startWidth + moveEvent.clientX - startX)
        )
        setSidebarWidth(next)
      }
      const onUp = () => {
        isDragging.current = false
        window.removeEventListener("mousemove", onMove)
        window.removeEventListener("mouseup", onUp)
      }
      window.addEventListener("mousemove", onMove)
      window.addEventListener("mouseup", onUp)
    },
    [sidebarWidth]
  )

  const pollWorkspaceSidecarIndexStatus = useCallback(async (workspaceId: string) => {
    for (let attempt = 0; attempt < WORKSPACE_INDEX_POLL_MAX_ATTEMPTS; attempt += 1) {
      try {
        const status = await sidecarFetch<WorkspaceSidecarIndexStatus>(
          `/workspaces/${workspaceId}/sidecar-index/status`
        )
        if (status.status === "indexed" || status.status === "disabled") {
          return
        }
      } catch {
        // Ignore transient polling failures and keep the UI responsive.
      }

      if (attempt < WORKSPACE_INDEX_POLL_MAX_ATTEMPTS - 1) {
        await new Promise<void>((resolve) =>
          window.setTimeout(resolve, WORKSPACE_INDEX_POLL_INTERVAL_MS)
        )
      }
    }
  }, [])

  const handleAddProject = useCallback(
    async (name: string, path: string) => {
      const createdWorkspace = await sidecarFetch<WorkspaceSummary>("/workspaces", {
        method: "POST",
        body: JSON.stringify({ name, realPath: path, pathLabel: path }),
      })
      await refreshWorkspaceState()
      if (createdWorkspace?.id) {
        void pollWorkspaceSidecarIndexStatus(createdWorkspace.id)
      }
    },
    [pollWorkspaceSidecarIndexStatus, refreshWorkspaceState]
  )

  const handleNewSession = useCallback(
    async (projectId?: string) => {
      const targetId = projectId ?? activeWorkspaceId
      if (targetId == null) return

      setActiveWorkspaceId(targetId)
      setActiveSessionId(null)
      setActiveDraftWorkspaceId(targetId)
    },
    [activeWorkspaceId]
  )

  const startRunEventStream = useCallback((runId: string, workspaceId: string, sessionId: string) => {
    eventSourceRef.current?.close()
    const source = new EventSource(`/api/sidecar/proxy?path=${encodeURIComponent(`/runs/${runId}/events`)}`)
    eventSourceRef.current = source

    const markRunInactiveIfCurrent = (targetRunId: string) => {
      if (sessionActiveRunIdByIdRef.current[sessionId] !== targetRunId) {
        return
      }
      setSessionRunStatusById((prev) => ({
        ...prev,
        [sessionId]: false,
      }))
      setSessionActiveRunIdById((prev) => ({
        ...prev,
        [sessionId]: null,
      }))
    }

    const closeSource = () => {
      source.close()
      if (eventSourceRef.current === source) {
        eventSourceRef.current = null
      }
      markRunInactiveIfCurrent(runId)
    }

    const updateActiveSession = (updateSession: (session: Session) => Session) => {
      setSessionsByWorkspace((prev) => updateSessionInWorkspace(prev, workspaceId, sessionId, updateSession))
    }

    const refreshSessionSummary = () => {
      void sidecarFetch<{ sessions: ServerSession[] }>(`/workspaces/${workspaceId}/sessions`)
        .then((response) => {
          const refreshed = response.sessions.find((session) => session.id === sessionId)
          if (!refreshed) {
            return
          }
          updateActiveSession((session) => ({
            ...session,
            ...refreshed,
            timelineEntries: session.timelineEntries,
          }))
        })
        .catch(() => undefined)
    }

    const applyTimelineEntryEvent = (event: Extract<RunStreamEvent, { type: "run.timeline-entry" }>) => {
      updateActiveSession((session) => ({
        ...session,
        timelineEntries: appendTimelineEntry(session.timelineEntries, event.entry),
      }))
    }

    const applyTerminalEvent = (event: Extract<RunStreamEvent, { type: "run.completed" | "run.failed" }>) => {
      refreshSessionSummary()
      setRunError(event.type === "run.failed" ? event.errorMessage : null)
      markRunInactiveIfCurrent(event.runId)
    }

    const applyStatusEvent = (event: Extract<RunStreamEvent, { type: "run.status" }>) => {
      if (event.status === "queued" || event.status === "running") {
        setSessionRunStatusById((prev) => ({
          ...prev,
          [sessionId]: true,
        }))
        setSessionActiveRunIdById((prev) => ({
          ...prev,
          [sessionId]: event.runId,
        }))
        return
      }
      markRunInactiveIfCurrent(event.runId)
    }

    const applyEvent = (event: RunStreamEvent) => {
      if (event.sessionId !== sessionId) {
        return
      }
      if (event.type === "run.timeline-entry") {
        applyTimelineEntryEvent(event)
        return
      }
      if (event.type === "run.status") {
        applyStatusEvent(event)
        return
      }
      if (event.type === "run.completed" || event.type === "run.failed") {
        applyTerminalEvent(event)
        return
      }
      if (event.type === "run.replay-ready") {
        markRunInactiveIfCurrent(event.runId)
        closeSource()
      }
    }

    const handleMessageEvent = (messageEvent: MessageEvent<string>) => {
      try {
        const envelope = parseRunEventEnvelope(
          JSON.stringify({
            id: messageEvent.lastEventId,
            event: messageEvent.type,
            data: JSON.parse(messageEvent.data),
          })
        )
        applyEvent(envelope.data)
      } catch {
        // ignore malformed stream events
      }
    }

    ;["run.status", "run.timeline-entry", "run.completed", "run.failed", "run.replay-ready"].forEach((eventName) => {
      source.addEventListener(eventName, handleMessageEvent as EventListener)
    })

    source.onerror = () => {
      closeSource()
    }
  }, [])

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeWorkspaceId || !selectedModel) return
      const isDraftMode = activeSessionId == null && activeDraftWorkspaceId === activeWorkspaceId

      setRunError(null)
      const attachmentIds = activeSessionId
        ? (attachmentsBySession[activeSessionId] ?? []).map((item) => item.id)
        : []
      const draftAttachmentIds = isDraftMode
        ? (draftAttachmentsByWorkspace[activeWorkspaceId] ?? []).map((item) => item.id)
        : []
      const requestSessionId = activeSessionId ?? undefined

      try {
        const runResponse = await sidecarFetch<{
          run: { id: string; status: string; finalAnswer: string | null }
          session: ServerSession
          entries: TimelineEntry[]
        }>("/runs", {
          method: "POST",
          body: JSON.stringify({
            ...(requestSessionId ? { sessionId: requestSessionId } : {}),
            workspaceId: activeWorkspaceId,
            connectionId: selectedModel.connectionId,
            modelId: selectedModel.modelId,
            ...(selectedReasoningEffort ? { reasoningEffort: selectedReasoningEffort } : {}),
            ...(attachmentIds.length > 0 ? { attachmentIds } : {}),
            ...(draftAttachmentIds.length > 0 ? { draftAttachmentIds } : {}),
            input: text,
          }),
        })

        const nextSession = mergeSessionWithLocalHistory(
          runResponse.session,
          activeSessionId
            ? (sessionsByWorkspace[activeWorkspaceId] ?? []).find((session) => session.id === activeSessionId)
            : undefined,
          runResponse.entries
        )

        setSessionsByWorkspace((prev) => {
          const existing = prev[activeWorkspaceId] ?? []
          if (activeSessionId) {
            return {
              ...prev,
              [activeWorkspaceId]: existing.map((session) =>
                session.id === activeSessionId ? { ...nextSession } : session
              ),
            }
          }
          return {
            ...prev,
            [activeWorkspaceId]: [nextSession, ...existing.filter((session) => session.id !== nextSession.id)],
          }
        })

        setAttachmentsBySession((prev) => ({
          ...prev,
          [runResponse.session.id]: [],
        }))
        if (isDraftMode) {
          setDraftAttachmentsByWorkspace((prev) => ({
            ...prev,
            [activeWorkspaceId]: [],
          }))
          setActiveDraftWorkspaceId(null)
        }
        setActiveSessionId(runResponse.session.id)
        setSessionRunStatusById((prev) => ({
          ...prev,
          [runResponse.session.id]: runResponse.run.status === "queued" || runResponse.run.status === "running",
        }))
        setSessionActiveRunIdById((prev) => ({
          ...prev,
          [runResponse.session.id]:
            runResponse.run.status === "queued" || runResponse.run.status === "running"
              ? runResponse.run.id
              : null,
        }))

        if (runResponse.run.status === "queued" || runResponse.run.status === "running") {
          startRunEventStream(runResponse.run.id, activeWorkspaceId, runResponse.session.id)
        }
      } catch (error) {
        if (error instanceof NSBotRequestError && error.payload) {
          const payload = error.payload as {
            detail?: string
            session?: ServerSession
            entries?: TimelineEntry[]
          }
          if (payload.session && payload.entries) {
            const payloadSession = mergeSessionWithLocalHistory(
              payload.session,
              activeSessionId
                ? (sessionsByWorkspace[activeWorkspaceId] ?? []).find((session) => session.id === activeSessionId)
                : undefined,
              payload.entries
            )
            setSessionsByWorkspace((prev) => {
              const existing = prev[activeWorkspaceId] ?? []
              if (activeSessionId) {
                return {
                  ...prev,
                  [activeWorkspaceId]: existing.map((session) =>
                    session.id === activeSessionId ? payloadSession : session
                  ),
                }
              }
              return {
                ...prev,
                [activeWorkspaceId]: [payloadSession, ...existing.filter((session) => session.id !== payloadSession.id)],
              }
            })
            setActiveSessionId(payload.session.id)
            setActiveDraftWorkspaceId(null)
          }
          if (activeSessionId) {
            setAttachmentsBySession((prev) => ({
              ...prev,
              [activeSessionId]: [],
            }))
          } else if (isDraftMode) {
            setDraftAttachmentsByWorkspace((prev) => ({
              ...prev,
              [activeWorkspaceId]: [],
            }))
          }
          setRunError(payload.detail ?? error.message)
        } else {
          setRunError(error instanceof Error ? error.message : "Failed to run request")
        }
        throw error
      }
    },
    [
      activeDraftWorkspaceId,
      activeSessionId,
      activeWorkspaceId,
      attachmentsBySession,
      draftAttachmentsByWorkspace,
      sessionsByWorkspace,
      selectedModel,
      selectedReasoningEffort,
      startRunEventStream,
    ]
  )

  const handleAttachFiles = useCallback(
    async (files: File[]) => {
      const isDraftMode =
        activeSessionId == null && activeWorkspaceId != null && activeDraftWorkspaceId === activeWorkspaceId

      if (!activeSessionId && !isDraftMode) {
        return
      }

      const uploadTargetId = activeSessionId ? `session:${activeSessionId}` : `draft:${activeWorkspaceId}`
      setUploadingAttachmentTargetId(uploadTargetId)

      try {
        const uploads = files.map(async (file) => {
          const formData = new FormData()
          formData.append("file", file)
          return sidecarFetch<ComposerAttachment | DraftAttachment>(
            activeSessionId
              ? `/sessions/${activeSessionId}/attachments`
              : `/workspaces/${activeWorkspaceId}/draft-attachments`,
            {
              method: "POST",
              body: formData,
            }
          )
        })

        const created = await Promise.all(uploads)
        if (activeSessionId) {
          setAttachmentsBySession((prev) => {
            const existing = prev[activeSessionId] ?? []
            const existingIds = new Set(existing.map((item) => item.id))
            const normalized = created as ComposerAttachment[]
            return {
              ...prev,
              [activeSessionId]: [
                ...existing,
                ...normalized.filter((item) => !existingIds.has(item.id)),
              ],
            }
          })
        } else if (activeWorkspaceId) {
          setDraftAttachmentsByWorkspace((prev) => {
            const existing = prev[activeWorkspaceId] ?? []
            const existingIds = new Set(existing.map((item) => item.id))
            const normalized = created as DraftAttachment[]
            return {
              ...prev,
              [activeWorkspaceId]: [
                ...existing,
                ...normalized.filter((item) => !existingIds.has(item.id)),
              ],
            }
          })
        }
      } finally {
        setUploadingAttachmentTargetId((current) => (current === uploadTargetId ? null : current))
      }
    },
    [activeDraftWorkspaceId, activeSessionId, activeWorkspaceId]
  )

  const handleRemoveAttachment = useCallback(
    async (attachmentId: string) => {
      const isDraftMode =
        activeSessionId == null && activeWorkspaceId != null && activeDraftWorkspaceId === activeWorkspaceId
      if (!activeSessionId && !isDraftMode) {
        return
      }
      if (activeSessionId) {
        await sidecarFetch<void>(`/sessions/${activeSessionId}/attachments/${attachmentId}`, {
          method: "DELETE",
        })
        setAttachmentsBySession((prev) => ({
          ...prev,
          [activeSessionId]: (prev[activeSessionId] ?? []).filter((attachment) => attachment.id !== attachmentId),
        }))
        return
      }
      if (!activeWorkspaceId) {
        return
      }
      await sidecarFetch<void>(`/workspaces/${activeWorkspaceId}/draft-attachments/${attachmentId}`, {
        method: "DELETE",
      })
      setDraftAttachmentsByWorkspace((prev) => ({
        ...prev,
        [activeWorkspaceId]: (prev[activeWorkspaceId] ?? []).filter((attachment) => attachment.id !== attachmentId),
      }))
    },
    [activeDraftWorkspaceId, activeSessionId, activeWorkspaceId]
  )

  const handleEditTimelineEntryAndRerun = useCallback(
    async (entryId: string, nextContent: string) => {
      if (!activeSessionId || !activeWorkspaceId || !selectedModel) {
        return
      }

      setRunError(null)

      try {
        const runResponse = await sidecarFetch<{
          run: { id: string; status: string; finalAnswer: string | null }
          session: ServerSession
          entries: TimelineEntry[]
        }>(`/sessions/${activeSessionId}/timeline/${entryId}/edit-and-run`, {
          method: "POST",
          body: JSON.stringify({
            content: nextContent,
            workspaceId: activeWorkspaceId,
            connectionId: selectedModel.connectionId,
            modelId: selectedModel.modelId,
            ...(selectedReasoningEffort ? { reasoningEffort: selectedReasoningEffort } : {}),
          }),
        })

        const nextSession = mergeSessionWithLocalHistory(
          runResponse.session,
          (sessionsByWorkspace[activeWorkspaceId] ?? []).find((session) => session.id === activeSessionId),
          runResponse.entries
        )

        setSessionsByWorkspace((prev) => ({
          ...prev,
          [activeWorkspaceId]: (prev[activeWorkspaceId] ?? []).map((session) =>
            session.id === activeSessionId ? nextSession : session
          ),
        }))

        if (runResponse.run.status === "queued" || runResponse.run.status === "running") {
          setSessionRunStatusById((prev) => ({
            ...prev,
            [activeSessionId]: true,
          }))
          setSessionActiveRunIdById((prev) => ({
            ...prev,
            [activeSessionId]: runResponse.run.id,
          }))
          startRunEventStream(runResponse.run.id, activeWorkspaceId, activeSessionId)
        }
      } catch (error) {
        setRunError(error instanceof Error ? error.message : "Failed to edit and rerun request")
        throw error
      }
    },
    [
      activeSessionId,
      activeWorkspaceId,
      selectedModel,
      selectedReasoningEffort,
      sessionsByWorkspace,
      startRunEventStream,
    ]
  )

  const handleRemoveProject = useCallback(
    async (projectId: string) => {
      await sidecarFetch(`/workspaces/${projectId}`, { method: "DELETE" })
      setWorkspaces((prev) => prev.filter((workspace) => workspace.id !== projectId))
      setSessionsByWorkspace((prev) => {
        const next = { ...prev }
        delete next[projectId]
        return next
      })
      setDraftAttachmentsByWorkspace((prev) => {
        const next = { ...prev }
        delete next[projectId]
        return next
      })
      if (activeWorkspaceId === projectId) {
        setActiveDraftWorkspaceId(null)
        setActiveWorkspaceId(null)
        setActiveSessionId(null)
      }
    },
    [activeWorkspaceId]
  )

  const handleRemoveSession = useCallback(
    async (sessionId: string, workspaceId: string) => {
      await sidecarFetch<void>(`/sessions/${sessionId}`, { method: "DELETE" })

      let nextActiveSessionId: string | null = activeSessionId
      setSessionsByWorkspace((prev) => {
        const remaining = (prev[workspaceId] ?? []).filter((session) => session.id !== sessionId)
        if (activeWorkspaceId === workspaceId && activeSessionId === sessionId) {
          nextActiveSessionId = remaining[0]?.id ?? null
        }
        return {
          ...prev,
          [workspaceId]: remaining,
        }
      })
      setAttachmentsBySession((prev) => {
        const next = { ...prev }
        delete next[sessionId]
        return next
      })

      if (activeWorkspaceId === workspaceId && activeSessionId === sessionId) {
        eventSourceRef.current?.close()
        eventSourceRef.current = null
        setActiveSessionId(nextActiveSessionId)
        setActiveDraftWorkspaceId(nextActiveSessionId == null ? workspaceId : null)
      }
    },
    [activeSessionId, activeWorkspaceId]
  )

  const projects = useMemo<Project[]>(
    () =>
      workspaces.map((workspace) => ({
        id: workspace.id,
        name: workspace.name,
        path: workspace.pathLabel,
        sessions: sessionsByWorkspace[workspace.id] ?? [],
      })),
    [sessionsByWorkspace, workspaces]
  )

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeWorkspaceId) ?? null,
    [projects, activeWorkspaceId]
  )

  useEffect(() => {
    if (activeSession?.activeConnectionId && activeSession.activeModelId) {
      const sessionSelection = {
        connectionId: activeSession.activeConnectionId,
        modelId: activeSession.activeModelId,
      }
      if (isSelectedModelAvailable(sessionSelection, modelOptionGroups)) {
        setSelectedModel(sessionSelection)
      }
    }
  }, [activeSession, modelOptionGroups])

  const handleSaveProvider = useCallback(
    async (payload: SaveProviderPayload, providerId?: string) => {
      const savedProvider = providerId
        ? await updateProvider(providerId, payload)
        : await createProvider(payload)

      const validationModelId = payload.preferredModelId ?? undefined
      let validationError: Error | null = null

      try {
        const validationResult = await validateProvider(savedProvider.id, {
          modelId: validationModelId,
        })

        if (!validationResult.ok) {
          validationError = new Error(
            validationResult.errorMessage ??
              validationResult.healthMessage ??
              "Provider validation failed"
          )
        }
      } catch (error) {
        validationError = error instanceof Error ? error : new Error("Provider validation failed")
      }

      await refreshProviderState()

      if (validationError) {
        throw validationError
      }
    },
    [refreshProviderState]
  )

  const handleRemoveProvider = useCallback(
    async (providerId: string) => {
      await deleteProvider(providerId)
      await refreshProviderState()
    },
    [refreshProviderState]
  )

  return (
    <div className="flex h-screen bg-background">
      <Sidebar
        projects={projects}
        activeProjectId={activeWorkspaceId}
        activeSessionId={activeSessionId}
        width={sidebarWidth}
        onAddProject={(name, path) => {
          void handleAddProject(name, path)
        }}
        onNewSession={(projectId) => {
          void handleNewSession(projectId)
        }}
        onRenameProject={async (projectId, name, pathLabel) => {
          await sidecarFetch(`/workspaces/${projectId}`, {
            method: "PATCH",
            body: JSON.stringify({ name, pathLabel }),
          })
          await refreshWorkspaceState()
        }}
        onSessionChange={(sessionId, projectId) => {
          setActiveWorkspaceId(projectId)
          setActiveSessionId(sessionId)
          setActiveDraftWorkspaceId(null)
        }}
        onRemoveProject={(projectId) => {
          void handleRemoveProject(projectId)
        }}
        onRemoveSession={(sessionId, projectId) => {
          void handleRemoveSession(sessionId, projectId)
        }}
        onSettingsOpen={() => setSettingsOpen(true)}
        onResizeStart={handleSidebarResizeStart}
      />
      <MainContent
        activeProject={activeProject}
        activeSession={activeSession}
        isDraftSession={isDraftSessionActive}
        onSendMessage={handleSendMessage}
        modelOptionGroups={modelOptionGroups}
        selectedModel={selectedModel}
        selectedReasoningEffort={selectedReasoningEffort}
        onSelectedModelChange={setSelectedModel}
        onSelectedReasoningEffortChange={setSelectedReasoningEffort}
        isLoadingModels={isLoadingProviders}
        providerError={providerError ?? workspaceError}
        runError={runError}
        hasMoreHistory={activeSession?.hasMoreHistory ?? false}
        isLoadingHistory={activeSession?.isLoadingHistory ?? false}
        onLoadEarlierTimeline={handleLoadEarlierTimeline}
        composerAttachments={
          activeSessionId
            ? attachmentsBySession[activeSessionId] ?? []
            : activeWorkspaceId != null && activeDraftWorkspaceId === activeWorkspaceId
              ? draftAttachmentsByWorkspace[activeWorkspaceId] ?? []
              : []
        }
        isUploadingAttachment={
          activeSessionId != null
            ? uploadingAttachmentTargetId === `session:${activeSessionId}`
            : activeWorkspaceId != null && activeDraftWorkspaceId === activeWorkspaceId
              ? uploadingAttachmentTargetId === `draft:${activeWorkspaceId}`
              : false
        }
        onAttachFiles={handleAttachFiles}
        onRemoveAttachment={handleRemoveAttachment}
        onEditTimelineEntryAndRerun={handleEditTimelineEntryAndRerun}
        onOpenSettings={() => setSettingsOpen(true)}
        isSessionRunning={isActiveSessionRunning}
        activeRunId={activeSessionRunId}
      />
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        providerCatalog={providerCatalog}
        providerConnections={providerConnections}
        onSaveProvider={handleSaveProvider}
        onRemoveProvider={handleRemoveProvider}
      />
    </div>
  )
}

async function sidecarFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/sidecar/proxy?path=${encodeURIComponent(path)}`, {
    ...init,
    headers: {
      ...(!(init?.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  })

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null
    const isSessionDeleteNotAllowed =
      response.status === 405 && init?.method === "DELETE" && path.startsWith("/sessions/")
    const fallbackMessage = isSessionDeleteNotAllowed
      ? "Session deletion is not supported by the connected sidecar yet. Please restart or upgrade the sidecar service, then try again."
      : `Request failed with status ${response.status}`
    throw new NSBotRequestError(
      typeof payload?.detail === "string"
        ? payload.detail === "Method Not Allowed" && isSessionDeleteNotAllowed
          ? fallbackMessage
          : payload.detail
        : fallbackMessage,
      response.status,
      payload
    )
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

class NSBotRequestError extends Error {
  status: number
  payload: Record<string, unknown> | null

  constructor(message: string, status: number, payload: Record<string, unknown> | null) {
    super(message)
    this.name = "NSBotRequestError"
    this.status = status
    this.payload = payload
  }
}
