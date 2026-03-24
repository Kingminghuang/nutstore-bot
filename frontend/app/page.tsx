"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { MainContent } from "@/components/main-content"
import { SettingsModal } from "@/components/settings-modal"
import { Sidebar } from "@/components/sidebar"
import {
  createProvider,
  deleteProvider,
  getModelOptions,
  getProviderCatalog,
  getProviders,
  updateProvider,
} from "@/lib/sidecar-client"
import {
  type ModelOptionGroup,
  type ProviderCatalogEntry,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
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

export default function Home() {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [sessionsByWorkspace, setSessionsByWorkspace] = useState<Record<string, Session[]>>({})
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogEntry[]>([])
  const [providerConnections, setProviderConnections] = useState<ProviderConnectionDetail[]>([])
  const [modelOptionGroups, setModelOptionGroups] = useState<ModelOptionGroup[]>([])
  const [selectedModel, setSelectedModel] = useState<SelectedModelRef | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
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
    if (!activeSessionId || !activeWorkspaceId) {
      return
    }

    const currentSession = sessionsByWorkspace[activeWorkspaceId]?.find(
      (session) => session.id === activeSessionId
    )
    if (currentSession?.messages.length) {
      return
    }

    void sidecarFetch<{ messages: Message[] }>(`/sessions/${activeSessionId}/messages`)
      .then((response) => {
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
  }, [activeSessionId, activeWorkspaceId, sessionsByWorkspace])

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
    [activeSessionId, activeWorkspaceId, selectedModel]
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

      const applyEvent = (event: RunStreamEvent) => {
        if (event.sessionId !== sessionId) {
          return
        }

        if (event.type === "run.delta") {
          setSessionsByWorkspace((prev) => ({
            ...prev,
            [workspaceId]: (prev[workspaceId] ?? []).map((session) => {
              if (session.id !== sessionId) {
                return session
              }

              const messages = [...session.messages]
              const lastMessage = messages[messages.length - 1]
              if (
                lastMessage &&
                lastMessage.role === "assistant" &&
                (lastMessage.stepId ?? null) === event.stepId &&
                (lastMessage.runId ?? null) === event.runId
              ) {
                messages[messages.length - 1] = {
                  ...lastMessage,
                  content: `${lastMessage.content}${event.text}`,
                }
              } else {
                messages.push({
                  id: `stream-${event.runId}-${event.sequence}`,
                  role: "assistant",
                  content: event.text,
                  createdAt: event.createdAt,
                  stepId: event.stepId,
                  runId: event.runId,
                })
              }

              return {
                ...session,
                messages,
              }
            }),
          }))
          return
        }

        if (event.type === "run.message") {
          setSessionsByWorkspace((prev) => ({
            ...prev,
            [workspaceId]: (prev[workspaceId] ?? []).map((session) => {
              if (session.id !== sessionId) {
                return session
              }

              const withoutStreamMessage = session.messages.filter(
                (message) =>
                  !(message.id.startsWith("stream-") && (message.runId ?? null) === event.runId)
              )
              const existingIndex = withoutStreamMessage.findIndex(
                (message) => message.id === event.messageId
              )
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
              return {
                ...session,
                messages: withoutStreamMessage,
              }
            }),
          }))
          return
        }

        if (event.type === "run.completed" || event.type === "run.failed") {
          void sidecarFetch<{ sessions: Omit<Session, "messages">[] }>(
            `/workspaces/${workspaceId}/sessions`
          )
            .then((response) => {
              const refreshed = response.sessions.find((session) => session.id === sessionId)
              if (!refreshed) {
                return
              }
              setSessionsByWorkspace((prev) => ({
                ...prev,
                [workspaceId]: (prev[workspaceId] ?? []).map((session) =>
                  session.id === sessionId
                    ? { ...session, ...refreshed, messages: session.messages }
                    : session
                ),
              }))
            })
            .catch(() => undefined)

          if (event.type === "run.failed") {
            setRunError(event.errorMessage)
          } else {
            setRunError(null)
          }
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
  const activeSession =
    activeProject?.sessions.find((session) => session.id === activeSessionId) ?? null

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
        onSendMessage={handleSendMessage}
        modelOptionGroups={modelOptionGroups}
        selectedModel={selectedModel}
        onSelectedModelChange={setSelectedModel}
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
