"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { invoke } from "@tauri-apps/api/core"

import {
  applyAssistantDraftChunk,
  createLocalSession,
  createOptimisticUserEntry,
  mergeTimelineEntriesWithLiveTurn,
  type LiveTurnStateBySession,
  updateLiveTurnBySession,
  upsertSession,
} from "@/app/live-turn-state"
import { usePermissionRequests } from "@/app/use-permission-requests"
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
  acpClient,
} from "@/shared/api/sidecar"
import { isTauriRuntime, sidecarRequest } from "@/shared/api/sidecar/sidecar-transport"
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
import type {
  ComposerAttachment,
  DraftAttachment,
  Project,
  Session,
  WorkspaceSummary,
} from "@/features/session"

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
  const [pendingSessionId, setPendingSessionId] = useState<string | null>(null)
  const [liveTurnBySession, setLiveTurnBySession] = useState<LiveTurnStateBySession>({})
  const [attachmentsBySession, setAttachmentsBySession] = useState<Record<string, ComposerAttachment[]>>({})
  const [draftAttachmentsByWorkspace, setDraftAttachmentsByWorkspace] = useState<Record<string, DraftAttachment[]>>({})
  const [uploadingAttachmentTargetId, setUploadingAttachmentTargetId] = useState<string | null>(null)
  const lastHydrationAttemptSessionIdRef = useRef<string | null>(null)
  const isDragging = useRef(false)
  const acpReadyRef = useRef(false)
  const {
    pendingPermissionRequest,
    resolvePermissionRequest,
    requestPermissionFromUser,
    cancelAllPendingPermissionRequests,
    cancelPendingPermissionRequestForSession,
    hasPendingPermissionRequestForSession,
    getPendingPermissionOptionId,
  } = usePermissionRequests({ setLiveTurnBySession })

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
      setLiveTurnBySession({})
      setAttachmentsBySession({})
      setDraftAttachmentsByWorkspace({})
      setActiveDraftWorkspaceId(null)
      setActiveWorkspaceId(null)
      setActiveSessionId(null)
    }
  }, [])

  const startLiveTurn = useCallback(
    (
      sessionId: string,
      text: string,
      options?: {
        truncatedAfterSequence?: number | null
      }
    ) => {
      setLiveTurnBySession((prev) =>
        updateLiveTurnBySession(prev, sessionId, () => ({
          optimisticEntries: [createOptimisticUserEntry(sessionId, text)],
          truncatedAfterSequence: options?.truncatedAfterSequence ?? null,
          assistantDraft: "",
          planEntries: [],
          toolCalls: [],
          waitingForPermission: false,
        }))
      )
    },
    []
  )

  const hydrateSessionAfterRun = useCallback(async (workspaceId: string, sessionId: string) => {
    const [refreshedTimeline, sessionsPayload] = await Promise.all([
      getSessionTimeline(sessionId, { limit: TIMELINE_PAGE_SIZE }),
      sidecarFetch<{ sessions: ServerSession[] }>(`/workspaces/${workspaceId}/sessions`),
    ])
    const refreshedSession = sessionsPayload.sessions.find((session) => session.id === sessionId)
    if (!refreshedSession) {
      throw new Error("session not found after prompt")
    }

    setSessionsByWorkspace((prev) => {
      const existing = (prev[workspaceId] ?? []).find((session) => session.id === sessionId)
      const nextSession = mergeSessionWithLocalHistory(refreshedSession, existing, refreshedTimeline.entries)
      return {
        ...prev,
        [workspaceId]: upsertSession(prev[workspaceId] ?? [], nextSession),
      }
    })
    setLiveTurnBySession((prev) => updateLiveTurnBySession(prev, sessionId, () => null))
    return refreshedSession
  }, [])

  useEffect(() => {
    void Promise.all([refreshProviderState(), refreshWorkspaceState()])
  }, [refreshProviderState, refreshWorkspaceState])

  useEffect(() => {
    if (acpReadyRef.current) {
      return
    }
    acpReadyRef.current = true
    let cancelled = false

    acpClient.onServerRequest(async (request) => {
      if (cancelled) {
        return { outcome: { outcome: "cancelled" } }
      }

      if (request.method === "session/request_permission") {
        const toolCall = request.params?.toolCall as Record<string, unknown> | undefined
        const options = Array.isArray(request.params?.options)
          ? request.params.options.map((option) => {
              const normalized = option as Record<string, unknown>
              return {
                optionId: String(normalized.optionId ?? ""),
                name: String(normalized.name ?? ""),
                kind: String(normalized.kind ?? ""),
              }
            })
          : []

        return requestPermissionFromUser({
          sessionId: String(request.params?.sessionId ?? ""),
          toolCallId: String(toolCall?.toolCallId ?? ""),
          title: String(toolCall?.title ?? "Permission required"),
          kind: String(toolCall?.kind ?? "other"),
          options,
        })
      }

      if (request.method === "fs/read_text_file") {
        if (!isTauriRuntime()) {
          return { content: "", error: "fs/read_text_file unavailable in browser runtime" }
        }
        const path = String(request.params?.path ?? "")
        if (!path.trim()) {
          return { content: "", error: "path is required" }
        }
        const content = await invoke<string>("acp_read_text_file", { path })
        return { content }
      }

      if (request.method === "fs/write_text_file") {
        if (!isTauriRuntime()) {
          return { error: "fs/write_text_file unavailable in browser runtime" }
        }
        const path = String(request.params?.path ?? "")
        const content = String(request.params?.content ?? "")
        if (!path.trim()) {
          return { error: "path is required" }
        }
        await invoke("acp_write_text_file", { path, content })
        return {}
      }

      return { outcome: { outcome: "cancelled" } }
    })
    acpClient.onNotification((notification) => {
      if (notification.method !== "session/update") {
        return
      }

      const sessionId = String(notification.params?.sessionId ?? "")
      const update = notification.params?.update as Record<string, unknown> | undefined
      const sessionUpdate = String(update?.sessionUpdate ?? "")
      if (!sessionId || !update) {
        return
      }

      if (sessionUpdate === "agent_message_chunk") {
        const content = update.content as Record<string, unknown> | undefined
        const text = String(content?.text ?? "")
        if (!text) {
          return
        }
        setLiveTurnBySession((prev) =>
          updateLiveTurnBySession(prev, sessionId, (liveTurn) => ({
            ...liveTurn,
            assistantDraft: applyAssistantDraftChunk(liveTurn.assistantDraft, text),
          }))
        )
        return
      }

      if (sessionUpdate === "plan") {
        const entries = Array.isArray(update.entries) ? update.entries : []
        setLiveTurnBySession((prev) =>
          updateLiveTurnBySession(prev, sessionId, (liveTurn) => ({
            ...liveTurn,
            planEntries: entries.map((entry, index) => {
              const normalized = entry as Record<string, unknown>
              return {
                id: `${sessionId}-plan-${index}`,
                content: String(normalized.content ?? ""),
                priority:
                  normalized.priority === "high" ||
                  normalized.priority === "medium" ||
                  normalized.priority === "low"
                    ? normalized.priority
                    : null,
                status:
                  normalized.status === "completed" || normalized.status === "failed"
                    ? normalized.status
                    : "pending",
              }
            }),
          }))
        )
        return
      }

      if (sessionUpdate === "tool_call") {
        const toolCallId = String(update.toolCallId ?? "")
        if (!toolCallId) {
          return
        }
        setLiveTurnBySession((prev) =>
          updateLiveTurnBySession(prev, sessionId, (liveTurn) => {
            const nextToolCall = {
              toolCallId,
              title: String(update.title ?? "Tool call"),
              kind: String(update.kind ?? "other"),
              status: "pending" as const,
            }
            const existingIndex = liveTurn.toolCalls.findIndex(
              (toolCall) => toolCall.toolCallId === toolCallId
            )
            if (existingIndex < 0) {
              return {
                ...liveTurn,
                toolCalls: [...liveTurn.toolCalls, nextToolCall],
              }
            }
            return {
              ...liveTurn,
              toolCalls: liveTurn.toolCalls.map((toolCall, index) =>
                index === existingIndex ? { ...toolCall, ...nextToolCall } : toolCall
              ),
            }
          })
        )
        return
      }

      if (sessionUpdate === "tool_call_update") {
        const toolCallId = String(update.toolCallId ?? "")
        const status = String(update.status ?? "pending")
        if (!toolCallId) {
          return
        }
        setLiveTurnBySession((prev) =>
          updateLiveTurnBySession(prev, sessionId, (liveTurn) => ({
            ...liveTurn,
            waitingForPermission:
              status === "pending" ? liveTurn.waitingForPermission : false,
            toolCalls: liveTurn.toolCalls.map((toolCall) =>
              toolCall.toolCallId === toolCallId &&
              (status === "pending" ||
                status === "completed" ||
                status === "failed" ||
                status === "cancelled")
                ? { ...toolCall, status }
                : toolCall
            ),
          }))
        )
      }
    })
    void acpClient
      .request("initialize", {
        protocolVersion: 1,
        clientCapabilities: {
          fs: {
            readTextFile: isTauriRuntime(),
            writeTextFile: isTauriRuntime(),
          },
          terminal: false,
        },
        clientInfo: {
          name: "nutstore-frontend",
          title: "Nutstore Frontend",
          version: "0.1.0",
        },
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      cancelAllPendingPermissionRequests()
    }
  }, [cancelAllPendingPermissionRequests, requestPermissionFromUser])

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
  const activeLiveTurn = useMemo(
    () => (activeSessionId ? liveTurnBySession[activeSessionId] ?? null : null),
    [activeSessionId, liveTurnBySession]
  )
  const activeTimelineEntries = useMemo(
    () => mergeTimelineEntriesWithLiveTurn(activeSession?.timelineEntries ?? [], activeLiveTurn),
    [activeLiveTurn, activeSession?.timelineEntries]
  )
  const isActiveSessionRunning = activeSessionId != null && pendingSessionId === activeSessionId

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

  const handleSendMessage = useCallback(
    async (text: string, options: { autoAllow: boolean }) => {
      if (!activeWorkspaceId || !selectedModel) return
      const isDraftMode = activeSessionId == null && activeDraftWorkspaceId === activeWorkspaceId

      setRunError(null)
      let targetSessionId = activeSessionId

      try {
        if (!targetSessionId) {
          const workspace = workspaces.find((item) => item.id === activeWorkspaceId)
          if (!workspace) {
            throw new Error("workspace not found")
          }

          const createdSession = await acpClient.request<{ sessionId: string }>("session/new", {
            cwd: workspace.realPath,
          })
          targetSessionId = createdSession.sessionId
          await acpClient.request("session/set_config_option", {
            sessionId: targetSessionId,
            configId: "model",
            value: selectedModel.modelId,
          })
          const localSession = createLocalSession(
            targetSessionId,
            activeWorkspaceId,
            selectedModel,
            text
          )
          setSessionsByWorkspace((prev) => ({
            ...prev,
            [activeWorkspaceId]: upsertSession(prev[activeWorkspaceId] ?? [], localSession),
          }))
          setActiveSessionId(targetSessionId)
        }

        if (!targetSessionId) {
          throw new Error("failed to resolve session")
        }

        startLiveTurn(targetSessionId, text)
        setSessionsByWorkspace((prev) =>
          updateSessionInWorkspace(prev, activeWorkspaceId, targetSessionId!, (session) => ({
            ...session,
            updatedAt: new Date().toISOString(),
            lastMessageAt: new Date().toISOString(),
            lastMessagePreview: text,
            activeConnectionId: selectedModel.connectionId,
            activeModelId: selectedModel.modelId,
          }))
        )
        setPendingSessionId(targetSessionId)

        await acpClient.request("session/prompt", {
          sessionId: targetSessionId,
          prompt: [{ type: "text", text }],
          _meta: {
            autoAllow: options.autoAllow,
            selectedReasoningEffort: selectedReasoningEffort ?? null,
          },
        })

        await hydrateSessionAfterRun(activeWorkspaceId, targetSessionId)

        setAttachmentsBySession((prev) => ({
          ...prev,
          [targetSessionId!]: [],
        }))
        if (isDraftMode) {
          setDraftAttachmentsByWorkspace((prev) => ({
            ...prev,
            [activeWorkspaceId]: [],
          }))
          setActiveDraftWorkspaceId(null)
        }
        setActiveSessionId(targetSessionId)
        setPendingSessionId(null)
      } catch (error) {
        setRunError(error instanceof Error ? error.message : "Failed to run request")
        setPendingSessionId(null)
        throw error
      }
    },
    [
      activeDraftWorkspaceId,
      activeSessionId,
      activeWorkspaceId,
      hydrateSessionAfterRun,
      selectedModel,
      selectedReasoningEffort,
      startLiveTurn,
    ]
  )

  const cancelSessionRun = useCallback(
    async (sessionId: string) => {
      resolvePermissionRequest({ outcome: { outcome: "cancelled" } }, sessionId)
      await acpClient.notify("session/cancel", { sessionId })
      setPendingSessionId((current) => (current === sessionId ? null : current))
    },
    [resolvePermissionRequest]
  )

  const handleCancelActiveRun = useCallback(async () => {
    if (!activeSessionId || pendingSessionId !== activeSessionId) {
      return
    }
    await cancelSessionRun(activeSessionId)
  }, [activeSessionId, cancelSessionRun, pendingSessionId])

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
    async (entryId: string, nextContent: string, options: { autoAllow: boolean }) => {
      if (!activeSessionId || !activeWorkspaceId || !selectedModel || !activeSession) {
        return
      }

      const editedEntry = activeSession.timelineEntries.find((entry) => entry.id === entryId)
      if (!editedEntry) {
        throw new Error("Message not found")
      }

      setRunError(null)
      setPendingSessionId(activeSessionId)
      startLiveTurn(activeSessionId, nextContent, {
        truncatedAfterSequence: editedEntry.sequenceNo,
      })
      setSessionsByWorkspace((prev) =>
        updateSessionInWorkspace(prev, activeWorkspaceId, activeSessionId, (session) => ({
          ...session,
          updatedAt: new Date().toISOString(),
          lastMessageAt: new Date().toISOString(),
          lastMessagePreview: nextContent,
        }))
      )

      try {
        await acpClient.request("session/edit_and_prompt", {
          sessionId: activeSessionId,
          entryId,
          prompt: [{ type: "text", text: nextContent }],
          _meta: {
            autoAllow: options.autoAllow,
            selectedReasoningEffort: selectedReasoningEffort ?? null,
          },
        })
        await hydrateSessionAfterRun(activeWorkspaceId, activeSessionId)
      } catch (error) {
        setRunError(error instanceof Error ? error.message : "Failed to edit and rerun")
        throw error
      } finally {
        setPendingSessionId(null)
      }
    },
    [
      activeSessionId,
      activeSession,
      activeWorkspaceId,
      hydrateSessionAfterRun,
      selectedModel,
      selectedReasoningEffort,
      startLiveTurn,
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
      setLiveTurnBySession((prev) => {
        const removableSessionIds = new Set((sessionsByWorkspace[projectId] ?? []).map((session) => session.id))
        const next = { ...prev }
        for (const sessionId of removableSessionIds) {
          delete next[sessionId]
        }
        return next
      })
      if (activeWorkspaceId === projectId) {
        if (pendingPermissionRequestRef.current?.sessionId) {
          resolvePermissionRequest({ outcome: { outcome: "cancelled" } })
        }
        setActiveDraftWorkspaceId(null)
        setActiveWorkspaceId(null)
        setActiveSessionId(null)
      }
    },
    [activeWorkspaceId, resolvePermissionRequest, sessionsByWorkspace]
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
      setLiveTurnBySession((prev) => updateLiveTurnBySession(prev, sessionId, () => null))

      if (activeWorkspaceId === workspaceId && activeSessionId === sessionId) {
        cancelPendingPermissionRequestForSession(sessionId)
        setActiveSessionId(nextActiveSessionId)
        setActiveDraftWorkspaceId(nextActiveSessionId == null ? workspaceId : null)
      }
    },
    [activeSessionId, activeWorkspaceId, cancelPendingPermissionRequestForSession]
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
        timelineEntries={activeTimelineEntries}
        liveTurn={activeLiveTurn}
        isDraftSession={isDraftSessionActive}
        onSendMessage={handleSendMessage}
        onCancelRun={handleCancelActiveRun}
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
            ? attachmentsBySession[activeSessionId] ??
              (activeWorkspaceId != null && activeDraftWorkspaceId === activeWorkspaceId
                ? draftAttachmentsByWorkspace[activeWorkspaceId] ?? []
                : [])
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
        pendingPermissionRequest={pendingPermissionRequest}
        onAllowPermissionRequest={() => {
          resolvePermissionRequest(
            {
              outcome: {
                outcome: "selected",
                optionId: getPendingPermissionOptionId("allow_once", "allow-once"),
              },
            },
            pendingPermissionRequest?.sessionId
          )
        }}
        onAllowAlwaysPermissionRequest={() => {
          resolvePermissionRequest(
            {
              outcome: {
                outcome: "selected",
                optionId: getPendingPermissionOptionId("allow_once", "allow-once"),
              },
            },
            pendingPermissionRequest?.sessionId
          )
        }}
        onRejectPermissionRequest={() => {
          resolvePermissionRequest(
            {
              outcome: {
                outcome: "selected",
                optionId: getPendingPermissionOptionId("reject_once", "reject-once"),
              },
            },
            pendingPermissionRequest?.sessionId
          )
        }}
        onCancelPermissionRequest={() => {
          if (
            activeSessionId != null &&
            hasPendingPermissionRequestForSession(activeSessionId) &&
            pendingPermissionRequest?.sessionId === activeSessionId
          ) {
            void cancelSessionRun(pendingPermissionRequest.sessionId)
            return
          }
          resolvePermissionRequest(
            { outcome: { outcome: "cancelled" } },
            pendingPermissionRequest?.sessionId
          )
        }}
        onOpenSettings={() => setSettingsOpen(true)}
        isSessionRunning={isActiveSessionRunning}
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
  const headers = new Headers(init?.headers)
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }

  const response = await sidecarRequest(path, {
    ...init,
    headers,
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
