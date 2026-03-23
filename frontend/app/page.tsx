"use client"

import { useState, useCallback, useRef } from "react"
import { Sidebar } from "@/components/sidebar"
import { MainContent } from "@/components/main-content"
import { SettingsModal } from "@/components/settings-modal"
import type { ConnectedProvider } from "@/lib/provider-settings"

export type Message = {
  id: number
  role: "user" | "agent"
  content: string
}

export type Session = {
  id: number
  label: string
  time: string
  messages: Message[]
}

export type Project = {
  id: number
  name: string
  path: string
  sessions: Session[]
}

const MOCK_RESPONSES = [
  "I've analyzed your request. Here's what I found in the codebase:\n\n```python\ndef process(input):\n    return input.strip().lower()\n```\n\nThis function handles the transformation you described. Let me know if you want me to expand it.",
  "Sure! I'll refactor that module. The key change is extracting the shared logic into a utility function, reducing duplication across 3 files.",
  "I found 2 potential issues:\n1. `config.json` is missing the `timeout` field — this may cause silent failures.\n2. The retry loop on line 48 has no backoff, which could hammer the API under load.",
  "Done! I've updated the types and regenerated the interface. The new `Session` type now includes `messages: Message[]` for full conversation history.",
  "Let me search the codebase for references to that pattern...\n\nFound 6 occurrences across `src/agent/`, `src/runner/`, and `tests/`. I'll update them all to use the new abstraction.",
  "The error stems from a missing `await` on line 23. Here's the fix:\n\n```ts\nconst result = await fetchData(id)\n```\n\nWithout `await`, `result` is a Promise object and `.map()` fails silently.",
  "I've written unit tests for the new feature. Coverage is now at 87% for this module. Run `pnpm test` to verify.",
  "Analysis complete. The bottleneck is the sequential loop in `processQueue`. Switching to `Promise.all()` should improve throughput by ~4x for typical workloads.",
]

let nextId = 1
const freshId = () => nextId++

const SIDEBAR_MIN = 160
const SIDEBAR_MAX = 480
const SIDEBAR_DEFAULT = 230

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProjectId, setActiveProjectId] = useState<number | null>(null)
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null)
  const [connectedProviders, setConnectedProviders] = useState<ConnectedProvider[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT)
  const isDragging = useRef(false)

  const handleSidebarResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDragging.current = true
    const startX = e.clientX
    const startWidth = sidebarWidth

    const onMove = (moveEvent: MouseEvent) => {
      if (!isDragging.current) return
      const next = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startWidth + moveEvent.clientX - startX))
      setSidebarWidth(next)
    }
    const onUp = () => {
      isDragging.current = false
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }, [sidebarWidth])

  /** Add a new project from folder picker result */
  const handleAddProject = useCallback((name: string, path: string) => {
    const newProject: Project = {
      id: freshId(),
      name,
      path,
      sessions: [],
    }
    setProjects((prev) => [...prev, newProject])
    setActiveProjectId(newProject.id)
    setActiveSessionId(null)
  }, [])

  /** Create a new session inside a specific project (or the active project) */
  const handleNewSession = useCallback(
    (projectId?: number) => {
      const targetId = projectId ?? activeProjectId
      if (targetId == null) return

      const newSession: Session = {
        id: freshId(),
        label: "New session",
        time: "now",
        messages: [],
      }
      setProjects((prev) =>
        prev.map((p) =>
          p.id === targetId ? { ...p, sessions: [newSession, ...p.sessions] } : p
        )
      )
      setActiveProjectId(targetId)
      setActiveSessionId(newSession.id)
    },
    [activeProjectId]
  )

  /** Send a message in the active session — appends user msg then a random mock response */
  const handleSendMessage = useCallback(
    (text: string) => {
      if (!activeSessionId || !activeProjectId) return

      const userMsg: Message = { id: freshId(), role: "user", content: text }
      const randomResponse =
        MOCK_RESPONSES[Math.floor(Math.random() * MOCK_RESPONSES.length)]
      const agentMsg: Message = { id: freshId(), role: "agent", content: randomResponse }

      setProjects((prev) =>
        prev.map((p) => {
          if (p.id !== activeProjectId) return p
          return {
            ...p,
            sessions: p.sessions.map((s) => {
              if (s.id !== activeSessionId) return s
              const isFirst = s.messages.length === 0
              return {
                ...s,
                label: isFirst ? text.slice(0, 40) + (text.length > 40 ? "…" : "") : s.label,
                time: "just now",
                messages: [...s.messages, userMsg, agentMsg],
              }
            }),
          }
        })
      )
    },
    [activeProjectId, activeSessionId]
  )

  /** Remove a project */
  const handleRemoveProject = useCallback(
    (projectId: number) => {
      setProjects((prev) => prev.filter((p) => p.id !== projectId))
      if (activeProjectId === projectId) {
        setActiveProjectId(null)
        setActiveSessionId(null)
      }
    },
    [activeProjectId]
  )

  const activeProject = projects.find((p) => p.id === activeProjectId) ?? null
  const activeSession =
    activeProject?.sessions.find((s) => s.id === activeSessionId) ?? null

  const handleSaveConnectedProvider = useCallback((provider: ConnectedProvider, previousProviderId?: string) => {
    setConnectedProviders((prev) => {
      const next = prev.filter(
        (item) => item.id !== provider.id && item.id !== previousProviderId,
      )
      return [provider, ...next]
    })
  }, [])

  const handleRemoveConnectedProvider = useCallback((providerId: string) => {
    setConnectedProviders((prev) => prev.filter((provider) => provider.id !== providerId))
  }, [])

  return (
    <div className="flex h-screen bg-background">
      <Sidebar
        projects={projects}
        activeProjectId={activeProjectId}
        activeSessionId={activeSessionId}
        width={sidebarWidth}
        onAddProject={handleAddProject}
        onNewSession={handleNewSession}
        onSessionChange={(sessionId, projectId) => {
          setActiveProjectId(projectId)
          setActiveSessionId(sessionId)
        }}
        onRemoveProject={handleRemoveProject}
        onSettingsOpen={() => setSettingsOpen(true)}
        onResizeStart={handleSidebarResizeStart}
      />
      <MainContent
        activeProject={activeProject}
        activeSession={activeSession}
        onSendMessage={handleSendMessage}
      />
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        connectedProviders={connectedProviders}
        onSaveConnectedProvider={handleSaveConnectedProvider}
        onRemoveConnectedProvider={handleRemoveConnectedProvider}
      />
    </div>
  )
}
