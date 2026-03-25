"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { MainContent } from "@/components/main-content"
import { SettingsModal } from "@/components/settings-modal"
import { Sidebar } from "@/components/sidebar"
import {
  createProvider,
  deleteProvider,
  getRunSteps,
  getModelOptions,
  getProviderCatalog,
  getProviders,
  updateProvider,
  type RunHistoryStep,
} from "@/lib/sidecar-client"
import {
  normalizeSelectedReasoningEffort,
  type ModelOptionGroup,
  type ProviderCatalogEntry,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
  type SelectedReasoningEffort,
  type SelectedModelRef,
  isSelectedModelAvailable,
} from "@/lib/provider-settings"
import { parseRunEventEnvelope, type RunStreamEvent } from "@/lib/run-events"

export type Message = {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  createdAt: string
  stepId?: string | null
  runId?: string | null
}

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
  messages: Message[]
}

export type RunStepsByRunId = Record<string, RunHistoryStep[]>

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

const SIDEBAR_MIN = 160
const SIDEBAR_MAX = 480
const SIDEBAR_DEFAULT = 230

function getMessageRunIds(messages: Message[]): string[] {
  return Array.from(new Set(messages.map((message) => message.runId).filter(Boolean))) as string[]
}

function mergeRunStepsByEntries(
  prev: RunStepsByRunId,
  entries: ReadonlyArray<readonly [string, RunHistoryStep[]]>
): RunStepsByRunId {
  return {
    ...prev,
    ...Object.fromEntries(entries),
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

function appendDeltaToMessages(
  messages: Message[],
  event: Extract<RunStreamEvent, { type: "run.delta" }>
): Message[] {
  const nextMessages = [...messages]
  const lastMessage = nextMessages[nextMessages.length - 1]
  if (
    lastMessage &&
    lastMessage.role === "assistant" &&
    (lastMessage.stepId ?? null) === event.stepId &&
    (lastMessage.runId ?? null) === event.runId
  ) {
    nextMessages[nextMessages.length - 1] = {
      ...lastMessage,
      content: `${lastMessage.content}${event.text}`,
    }
    return nextMessages
  }

  nextMessages.push({
    id: `stream-${event.runId}-${event.sequence}`,
    role: "assistant",
    content: event.text,
    createdAt: event.createdAt,
    stepId: event.stepId,
    runId: event.runId,
  })
  return nextMessages
}

function applyRunMessageToMessages(
  messages: Message[],
  event: Extract<RunStreamEvent, { type: "run.message" }>
): Message[] {
  const withoutStreamMessage = messages.filter(
    (message) => !(message.id.startsWith("stream-") && (message.runId ?? null) === event.runId)
  )
  const existingIndex = withoutStreamMessage.findIndex((message) => message.id === event.messageId)
  const nextMessage = {
    id: event.messageId,
    role: event.role,
    content: event.content,
    createdAt: event.createdAt,
    stepId: event.stepId,
    runId: event.runId,
  }

  if (existingIndex >= 0) {
    withoutStreamMessage[existingIndex] = nextMessage
  } else {
    withoutStreamMessage.push(nextMessage)
  }

  return withoutStreamMessage
}

export default function Home() {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [sessionsByWorkspace, setSessionsByWorkspace] = useState<Record<string, Session[]>>({})
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogEntry[]>([])
  const [providerConnections, setProviderConnections] = useState<ProviderConnectionDetail[]>([])
  const [modelOptionGroups, setModelOptionGroups] = useState<ModelOptionGroup[]>([])
  const [selectedModel, setSelectedModel] = useState<SelectedModelRef | null>(null)
  const [selectedReasoningEffort, setSelectedReasoningEffort] =
    useState<SelectedReasoningEffort>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [runStepsByRunId, setRunStepsByRunId] = useState<RunStepsByRunId>({})
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT)
  const [isLoadingProviders, setIsLoadingProviders] = useState(true)
  const [providerError, setProviderError] = useState<string | null>(null)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
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
      setProviderError(
        error instanceof Error ? error.message : "Failed to load provider data"
      )
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
          const sessionsResponse = await sidecarFetch<{ sessions: Omit<Session, "messages">[] }>(
            `/workspaces/${workspace.id}/sessions`
          )
          return [
            workspace.id,
            sessionsResponse.sessions.map((session) => ({ ...session, messages: [] })),
          ] as const
        })
      )
      const nextSessionsByWorkspace = Object.fromEntries(sessionsEntries)
      setSessionsByWorkspace(nextSessionsByWorkspace)

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
    } catch (error) {
      setWorkspaceError(
        error instanceof Error ? error.message : "Failed to load workspaces"
      )
      setWorkspaces([])
      setSessionsByWorkspace({})
      setRunStepsByRunId({})
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

  useEffect(() => {
    if (!activeSessionId || !activeWorkspaceId || activeSession == null) {
      return
    }

    if (activeSession.messages.length > 0) {
      return
    }

    let cancelled = false

    void sidecarFetch<{ messages: Message[] }>(`/sessions/${activeSessionId}/messages`)
      .then((response) => {
        if (cancelled) {
          return
        }
        const runIds = getMessageRunIds(response.messages)
        setRunStepsByRunId((prev) => {
          const next = { ...prev }
          for (const key of Object.keys(next)) {
            if (!runIds.includes(key)) {
              delete next[key]
            }
          }
          return next
        })
        setSessionsByWorkspace((prev) => ({
          ...prev,
          [activeWorkspaceId]: (prev[activeWorkspaceId] ?? []).map((session) =>
            session.id === activeSessionId
              ? { ...session, messages: response.messages }
              : session
          ),
        }))
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [activeSession, activeSessionId, activeWorkspaceId])

  useEffect(() => {
    if (!activeSession) {
      return
    }

    const runIds = getMessageRunIds(activeSession.messages)
    const missingRunIds = runIds.filter((runId) => runStepsByRunId[runId] == null)
    if (missingRunIds.length === 0) {
      return
    }

    let cancelled = false

    void Promise.all(
      missingRunIds.map(async (runId) => {
        const stepsResponse = await getRunSteps(runId)
        return [runId, stepsResponse.steps] as const
      })
    )
      .then((entries) => {
        if (cancelled) {
          return
        }
        setRunStepsByRunId((prev) => mergeRunStepsByEntries(prev, entries))
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [activeSession, runStepsByRunId])

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

  const handleAddProject = useCallback(
    async (name: string, path: string) => {
      await sidecarFetch("/workspaces", {
        method: "POST",
        body: JSON.stringify({
          name,
          realPath: path,
          pathLabel: path,
        }),
      })
      await refreshWorkspaceState()
    },
    [refreshWorkspaceState]
  )

  const handleNewSession = useCallback(
    async (projectId?: string) => {
      const targetId = projectId ?? activeWorkspaceId
      if (targetId == null || !selectedModel) return

      const session = (await sidecarFetch<Omit<Session, "messages">>(`/workspaces/${targetId}/sessions`, {
        method: "POST",
        body: JSON.stringify({
          connectionId: selectedModel.connectionId,
          modelId: selectedModel.modelId,
        }),
      }))

      setSessionsByWorkspace((prev) => ({
        ...prev,
        [targetId]: [{ ...session, messages: [] }, ...(prev[targetId] ?? [])],
      }))
      setActiveWorkspaceId(targetId)
      setActiveSessionId(session.id)
    },
    [activeWorkspaceId, selectedModel]
  )

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeSessionId || !activeWorkspaceId || !selectedModel) return

      setRunError(null)

      try {
        const runResponse = await sidecarFetch<{
          run: {
            id: string
            status: string
            finalAnswer: string | null
          }
          session: Omit<Session, "messages">
          messages: Message[]
        }>(
          "/runs",
          {
            method: "POST",
            body: JSON.stringify({
              sessionId: activeSessionId,
              workspaceId: activeWorkspaceId,
              connectionId: selectedModel.connectionId,
              modelId: selectedModel.modelId,
              ...(selectedReasoningEffort
                ? { reasoningEffort: selectedReasoningEffort }
                : {}),
              input: text,
            }),
          }
        )

        setSessionsByWorkspace((prev) => ({
          ...prev,
          [activeWorkspaceId]: (prev[activeWorkspaceId] ?? []).map((session) =>
            session.id === activeSessionId
              ? {
                  ...runResponse.session,
                  messages: runResponse.messages.map((message) => ({
                    ...message,
                    role: message.role,
                  })),
                }
              : session
          ),
        }))
        setRunStepsByRunId((prev) => ({
          ...prev,
          [runResponse.run.id]: [],
        }))

        if (runResponse.run.status === "queued" || runResponse.run.status === "running") {
          startRunEventStream(runResponse.run.id, activeWorkspaceId, activeSessionId)
        }
      } catch (error) {
        if (error instanceof SidecarRequestError && error.payload) {
          const payload = error.payload as {
            detail?: string
            session?: Omit<Session, "messages">
            messages?: Message[]
          }
          if (payload.session && payload.messages) {
            setSessionsByWorkspace((prev) => ({
              ...prev,
              [activeWorkspaceId]: (prev[activeWorkspaceId] ?? []).map((session) =>
                session.id === activeSessionId
                  ? {
                      ...payload.session,
                      messages: payload.messages.map((message) => ({
                        ...message,
                        role: message.role,
                      })),
                    }
                  : session
              ),
            }))
          }
          setRunError(payload.detail ?? error.message)
        } else {
          setRunError(error instanceof Error ? error.message : "Failed to run request")
        }
        throw error
      }
    },
    [activeSessionId, activeWorkspaceId, selectedModel, selectedReasoningEffort]
  )

  const startRunEventStream = useCallback(
    (runId: string, workspaceId: string, sessionId: string) => {
      eventSourceRef.current?.close()
      const source = new EventSource(
        `/api/sidecar/proxy?path=${encodeURIComponent(`/runs/${runId}/events`)}`
      )
      eventSourceRef.current = source

      const closeSource = () => {
        source.close()
        if (eventSourceRef.current === source) {
          eventSourceRef.current = null
        }
      }

      const updateActiveSession = (updateSession: (session: Session) => Session) => {
        setSessionsByWorkspace((prev) =>
          updateSessionInWorkspace(prev, workspaceId, sessionId, updateSession)
        )
      }

      const refreshSessionSummary = () => {
        void sidecarFetch<{ sessions: Omit<Session, "messages">[] }>(`/workspaces/${workspaceId}/sessions`)
          .then((response) => {
            const refreshed = response.sessions.find((session) => session.id === sessionId)
            if (!refreshed) {
              return
            }

            updateActiveSession((session) => ({
              ...session,
              ...refreshed,
              messages: session.messages,
            }))
          })
          .catch(() => undefined)
      }

      const applyDeltaEvent = (event: Extract<RunStreamEvent, { type: "run.delta" }>) => {
        updateActiveSession((session) => ({
          ...session,
          messages: appendDeltaToMessages(session.messages, event),
        }))
      }

      const applyMessageEvent = (event: Extract<RunStreamEvent, { type: "run.message" }>) => {
        updateActiveSession((session) => ({
          ...session,
          messages: applyRunMessageToMessages(session.messages, event),
        }))
      }

      const applyTerminalEvent = (
        event: Extract<RunStreamEvent, { type: "run.completed" | "run.failed" }>
      ) => {
        refreshSessionSummary()
        setRunError(event.type === "run.failed" ? event.errorMessage : null)
      }

      const applyEvent = (event: RunStreamEvent) => {
        if (event.sessionId !== sessionId) {
          return
        }

        if (event.type === "run.delta") {
          applyDeltaEvent(event)
          return
        }

        if (event.type === "run.message") {
          applyMessageEvent(event)
          return
        }

        if (event.type === "run.completed" || event.type === "run.failed") {
          applyTerminalEvent(event)
          return
        }

        if (event.type === "run.replay-ready") {
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

      ;[
        "run.delta",
        "run.message",
        "run.completed",
        "run.failed",
        "run.replay-ready",
      ].forEach((eventName) => {
        source.addEventListener(eventName, handleMessageEvent as EventListener)
      })

      source.onerror = () => {
        closeSource()
      }
    },
    []
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
      if (activeWorkspaceId === projectId) {
        setActiveWorkspaceId(null)
        setActiveSessionId(null)
      }
    },
    [activeWorkspaceId]
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
      if (providerId) {
        await updateProvider(providerId, payload)
      } else {
        await createProvider(payload)
      }
      await refreshProviderState()
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
            body: JSON.stringify({
              name,
              pathLabel,
            }),
          })
          await refreshWorkspaceState()
        }}
        onSessionChange={(sessionId, projectId) => {
          setActiveWorkspaceId(projectId)
          setActiveSessionId(sessionId)
        }}
        onRemoveProject={(projectId) => {
          void handleRemoveProject(projectId)
        }}
        onSettingsOpen={() => setSettingsOpen(true)}
        onResizeStart={handleSidebarResizeStart}
      />
      <MainContent
        activeProject={activeProject}
        activeSession={activeSession}
        runStepsByRunId={runStepsByRunId}
        onSendMessage={handleSendMessage}
        modelOptionGroups={modelOptionGroups}
        selectedModel={selectedModel}
        selectedReasoningEffort={selectedReasoningEffort}
        onSelectedModelChange={setSelectedModel}
        onSelectedReasoningEffortChange={setSelectedReasoningEffort}
        isLoadingModels={isLoadingProviders}
        providerError={providerError ?? workspaceError}
        runError={runError}
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
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  })

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null
    throw new SidecarRequestError(
      typeof payload?.detail === "string"
        ? payload.detail
        : `Request failed with status ${response.status}`,
      response.status,
      payload
    )
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

class SidecarRequestError extends Error {
  status: number
  payload: Record<string, unknown> | null

  constructor(message: string, status: number, payload: Record<string, unknown> | null) {
    super(message)
    this.name = "SidecarRequestError"
    this.status = status
    this.payload = payload
  }
}
