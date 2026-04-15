import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import Home from "@/app/page"

const sidecarApiMocks = vi.hoisted(() => {
  let notificationHandler: ((notification: { method: string; params?: Record<string, unknown> }) => void) | null = null
  let serverRequestHandler:
    | ((request: { id: number; method: string; params?: Record<string, unknown> }) => Promise<unknown>)
    | null = null
  let pendingPromptResolves: Array<(value: unknown) => void> = []

  return {
    acpRequest: vi.fn((method: string) => {
      if (method === "workspace/list") {
        return Promise.resolve({
          workspaces: [
            {
              id: "ws_1",
              name: "Project 1",
              pathLabel: "/tmp/project",
              realPath: "/tmp/project",
              createdAt: "2026-01-01T00:00:00Z",
              updatedAt: "2026-01-01T00:00:00Z",
            },
          ],
        })
      }
      if (method === "workspace/sessions/list") {
        return Promise.resolve({ sessions: [baseSession] })
      }
      if (method === "attachment/list") {
        return Promise.resolve({ attachments: [] })
      }
      if (method === "draft_attachment/list") {
        return Promise.resolve({ draftAttachments: [] })
      }
      if (method === "initialize") {
        return Promise.resolve({ protocolVersion: 1 })
      }
      if (method === "session/prompt") {
        return new Promise((resolve) => {
          pendingPromptResolves.push(resolve)
        })
      }
      if (method === "session/edit_and_prompt") {
        return new Promise((resolve) => {
          pendingPromptResolves.push(resolve)
        })
      }
      if (method === "session/new") {
        return Promise.resolve({ sessionId: "sess_1" })
      }
      if (method === "session/set_config_option") {
        return Promise.resolve({ configOptions: [] })
      }
      return Promise.resolve({})
    }),
    acpNotify: vi.fn(async () => undefined),
    acpOnServerRequest: vi.fn((handler) => {
      serverRequestHandler = handler
    }),
    acpOnNotification: vi.fn((handler) => {
      notificationHandler = handler
    }),
    emitNotification(notification: { method: string; params?: Record<string, unknown> }) {
      notificationHandler?.(notification)
    },
    invokeServerRequest(request: { id: number; method: string; params?: Record<string, unknown> }) {
      if (!serverRequestHandler) {
        throw new Error("server request handler not registered")
      }
      return serverRequestHandler(request)
    },
    resolvePrompt(result: unknown = { stopReason: "end_turn" }) {
      pendingPromptResolves.shift()?.(result)
    },
    reset() {
      notificationHandler = null
      serverRequestHandler = null
      pendingPromptResolves = []
    },
    getSessionTimeline: vi.fn(),
    loadSession: vi.fn(async () => ({ configOptions: [] })),
  }
})

vi.mock("@/shared/api/sidecar", () => ({
  getProviderCatalog: vi.fn(async () => ({ providers: [] })),
  getProviders: vi.fn(async () => ({ connections: [] })),
  getModelOptions: vi.fn(async () => ({
    groups: [
      {
        connectionId: "prov_openai",
        providerLabel: "OpenAI",
        models: [
          {
            modelId: "gpt-5.4",
            label: "gpt-5.4",
            description: "",
            reasoningEffortValues: ["low", "medium"],
          },
        ],
      },
    ],
    defaultSelection: { connectionId: "prov_openai", modelId: "gpt-5.4" },
  })),
  getSessionTimeline: sidecarApiMocks.getSessionTimeline,
  loadSession: sidecarApiMocks.loadSession,
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  listWorkspaces: vi.fn(async () => ({
    workspaces: [
      {
        id: "ws_1",
        name: "Project 1",
        pathLabel: "/tmp/project",
        realPath: "/tmp/project",
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    ],
  })),
  listWorkspaceSessions: vi.fn(async () => ({ sessions: [baseSession] })),
  listAttachments: vi.fn(async () => ({ attachments: [] })),
  listDraftAttachments: vi.fn(async () => ({ draftAttachments: [] })),
  createWorkspace: vi.fn(),
  updateWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  createAttachment: vi.fn(),
  createDraftAttachment: vi.fn(),
  deleteAttachment: vi.fn(),
  deleteDraftAttachment: vi.fn(),
  deleteSession: vi.fn(),
  workspaceSidecarIndexStatus: vi.fn(),
  projectConversationEvents: vi.fn((sessionId: string, events: Array<Record<string, unknown>>) =>
    events.map((event) => ({
      id: String(event.eventId ?? `evt-${sessionId}`),
      eventId: String(event.eventId ?? `evt-${sessionId}`),
      sessionId,
      runId: null,
      sequenceNo: Number(event.sequenceNo ?? 0),
      entryKind: event.eventType === "agent_thought_chunk" ? "thinking" : String((event as { entryKind?: unknown }).entryKind ?? "user_input"),
      displayRole: String((event as { displayRole?: unknown }).displayRole ?? "user"),
      stepId: null,
      stepNumber: null,
      contentText:
        (event.payload as { params?: { update?: { content?: { text?: string } } } } | undefined)?.params?.update?.content?.text ??
        String((event as { contentText?: unknown }).contentText ?? ""),
      createdAt: String(event.createdAt ?? "2026-01-01T00:00:00Z"),
    }))
  ),
  validateProvider: vi.fn(),
  acpClient: {
    request: sidecarApiMocks.acpRequest,
    notify: sidecarApiMocks.acpNotify,
    onServerRequest: sidecarApiMocks.acpOnServerRequest,
    onNotification: sidecarApiMocks.acpOnNotification,
  },
}))

const baseSession = {
  id: "sess_existing",
  workspaceId: "ws_1",
  title: "Existing session",
  titleSource: "placeholder",
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  lastMessageAt: null,
  messageCount: 0,
  lastMessagePreview: null,
  activeConnectionId: "prov_openai",
  activeModelId: "gpt-5.4",
}

beforeEach(() => {
  sidecarApiMocks.reset()
  vi.clearAllMocks()
  sidecarApiMocks.getSessionTimeline.mockReset()
  ;(window as Window & { __TAURI__?: object }).__TAURI__ = {}
})

describe("Home page ACP bootstrap", () => {
  it("initializes ACP and renders workspace", async () => {
    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)

    await waitFor(() => {
      expect(sidecarApiMocks.acpRequest).toHaveBeenCalledWith(
        "initialize",
        expect.objectContaining({ protocolVersion: 1 })
      )
    })

    const projectLabels = await screen.findAllByText("Project 1")
    expect(projectLabels.length).toBeGreaterThan(0)
  })

  it("shows the user message immediately and renders streaming updates before the turn completes", async () => {
    sidecarApiMocks.getSessionTimeline
      .mockResolvedValueOnce({ events: [], pagination: { hasMore: false, nextBeforeSequence: null } })
      .mockResolvedValueOnce({
        events: [
          {
            id: "entry_user_1",
            eventId: "evt_stream_user_1",
            sessionId: "sess_existing",
            runId: null,
            sequenceNo: 1,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Stream this response",
            createdAt: "2026-01-01T00:00:01Z",
          },
          {
            id: "entry_answer_1",
            sessionId: "sess_existing",
            runId: null,
            sequenceNo: 2,
            entryKind: "final_answer",
            displayRole: "assistant",
            stepId: null,
            stepNumber: null,
            contentText: "Hello world",
            createdAt: "2026-01-01T00:00:02Z",
          },
        ],
        pagination: { hasMore: false, nextBeforeSequence: null },
      })

    render(<Home />)
    await screen.findAllByText("Project 1")

    const input = await screen.findByPlaceholderText("Ask for follow-up changes")
    fireEvent.change(input, { target: { value: "Stream this response" } })
    fireEvent.click(screen.getByLabelText("Send"))

    expect(screen.getByText("Stream this response")).toBeInTheDocument()
    expect((screen.getByPlaceholderText("Ask for follow-up changes") as HTMLInputElement).value).toBe("")

    act(() => {
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "plan",
            entries: [{ content: "Inspect the workspace", priority: "medium", status: "pending" }],
          },
        },
      })
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "agent_thought_chunk",
            content: { type: "text", text: "Need to inspect tool output first" },
          },
        },
      })
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "available_commands_update",
            availableCommands: [
              {
                name: "write",
                description: "Write file",
                input: { hint: "path" },
              },
            ],
          },
        },
      })
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "tool_call",
            toolCallId: "tool_1",
            title: "write",
            kind: "write",
            status: "pending",
          },
        },
      })
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "agent_message_chunk",
            content: { type: "text", text: "Hello" },
          },
        },
      })
    })

    expect(screen.getByText("Inspect the workspace")).toBeInTheDocument()
    expect(screen.getAllByText("write").length).toBeGreaterThan(0)
    expect(screen.getByText("Need to inspect tool output first")).toBeInTheDocument()
    expect(screen.getByText(/Available Commands/)).toBeInTheDocument()
    expect(screen.getByText("Hello")).toBeInTheDocument()

    await act(async () => {
      sidecarApiMocks.resolvePrompt()
    })

    await screen.findByText("Hello world")
  })

  it("merges tool_call_update payload details into ACP tool call card", async () => {
    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)
    await screen.findAllByText("Project 1")

    act(() => {
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "tool_call",
            toolCallId: "tool_2",
            title: "write",
            kind: "write",
            status: "pending",
          },
        },
      })
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "tool_call_update",
            toolCallId: "tool_2",
            status: "completed",
            rawInput: { command: "cat package.json" },
            content: [
              {
                type: "content",
                content: {
                  type: "text",
                  text: "Updated result body",
                },
              },
            ],
          },
        },
      })
    })

    expect(screen.getByText(/Tool Call ID: tool_2/)).toBeInTheDocument()
    expect(screen.getByText(/cat package.json/)).toBeInTheDocument()
    expect(screen.getByText("Updated result body")).toBeInTheDocument()
  })

  it("renders permission card and returns the selected outcome", async () => {
    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)
    await screen.findAllByText("Project 1")

    let requestPromise!: Promise<unknown>
    await act(async () => {
      requestPromise = sidecarApiMocks.invokeServerRequest({
        id: 42,
        method: "session/request_permission",
        params: {
          sessionId: "sess_existing",
          toolCall: {
            toolCallId: "tool_1",
            title: "write",
            kind: "write",
            status: "pending",
          },
          options: [
            { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
            { optionId: "allow-always", name: "Allow always", kind: "allow_always" },
            { optionId: "reject-once", name: "Reject", kind: "reject_once" },
          ],
        },
      }) as Promise<unknown>
    })

    expect(await screen.findByText("Permission required")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }))

    await expect(requestPromise).resolves.toEqual({
      outcome: {
        outcome: "selected",
        optionId: "allow-once",
      },
    })
  })

  it("returns reject-once when the user rejects a permission request", async () => {
    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)
    await screen.findAllByText("Project 1")

    let requestPromise!: Promise<unknown>
    await act(async () => {
      requestPromise = sidecarApiMocks.invokeServerRequest({
        id: 43,
        method: "session/request_permission",
        params: {
          sessionId: "sess_existing",
          toolCall: {
            toolCallId: "tool_2",
            title: "edit",
            kind: "edit",
            status: "pending",
          },
          options: [
            { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
            { optionId: "allow-always", name: "Allow always", kind: "allow_always" },
            { optionId: "reject-once", name: "Reject", kind: "reject_once" },
          ],
        },
      }) as Promise<unknown>
    })

    expect(await screen.findByText("Permission required")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Deny" }))

    await expect(requestPromise).resolves.toEqual({
      outcome: {
        outcome: "selected",
        optionId: "reject-once",
      },
    })
  })

  it("cancels the run and resolves permission as cancelled when the user chooses cancel run", async () => {
    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)
    await screen.findAllByText("Project 1")

    let requestPromise!: Promise<unknown>
    await act(async () => {
      requestPromise = sidecarApiMocks.invokeServerRequest({
        id: 44,
        method: "session/request_permission",
        params: {
          sessionId: "sess_existing",
          toolCall: {
            toolCallId: "tool_3",
            title: "python_exec_agent",
            kind: "python_exec_agent",
            status: "pending",
          },
          options: [
            { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
            { optionId: "allow-always", name: "Allow always", kind: "allow_always" },
            { optionId: "reject-once", name: "Reject", kind: "reject_once" },
          ],
        },
      }) as Promise<unknown>
    })

    expect(await screen.findByText("Permission required")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }))

    await expect(requestPromise).resolves.toEqual({
      outcome: {
        outcome: "cancelled",
      },
    })
    expect(sidecarApiMocks.acpNotify).toHaveBeenCalledWith("session/cancel", {
      sessionId: "sess_existing",
    })
  })

  it("switches the composer back to auto-allow when the user chooses allow always", async () => {
    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)
    await screen.findAllByText("Project 1")

    fireEvent.pointerDown(screen.getByText("Auto-allow"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Ask first" }))
    expect(screen.getByText("Ask first")).toBeInTheDocument()

    let requestPromise!: Promise<unknown>
    await act(async () => {
      requestPromise = sidecarApiMocks.invokeServerRequest({
        id: 45,
        method: "session/request_permission",
        params: {
          sessionId: "sess_existing",
          toolCall: {
            toolCallId: "tool_4",
            title: "write",
            kind: "write",
            status: "pending",
          },
          options: [
            { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
            { optionId: "allow-always", name: "Allow always", kind: "allow_always" },
            { optionId: "reject-once", name: "Reject", kind: "reject_once" },
          ],
        },
      }) as Promise<unknown>
    })

    expect(await screen.findByText("Permission required")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Allow always" }))

    await expect(requestPromise).resolves.toEqual({
      outcome: {
        outcome: "selected",
        optionId: "allow-always",
      },
    })
    expect(screen.getByText("Auto-allow")).toBeInTheDocument()
  })

  it("truncates the old turn and streams the replacement content during edit-and-rerun", async () => {
    sidecarApiMocks.getSessionTimeline
      .mockResolvedValueOnce({
        events: [
          {
            id: "entry_user_1",
            eventId: "evt_user_1",
            sessionId: "sess_existing",
            runId: null,
            sequenceNo: 1,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Old prompt",
            createdAt: "2026-01-01T00:00:01Z",
          },
          {
            id: "entry_answer_1",
            sessionId: "sess_existing",
            runId: null,
            sequenceNo: 2,
            entryKind: "final_answer",
            displayRole: "assistant",
            stepId: null,
            stepNumber: null,
            contentText: "Old answer",
            createdAt: "2026-01-01T00:00:02Z",
          },
        ],
        pagination: { hasMore: false, nextBeforeSequence: null },
      })
      .mockResolvedValueOnce({
        events: [
          {
            id: "entry_user_2",
            eventId: "evt_user_2",
            sessionId: "sess_existing",
            runId: null,
            sequenceNo: 3,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Updated prompt",
            createdAt: "2026-01-01T00:01:01Z",
          },
          {
            id: "entry_answer_2",
            sessionId: "sess_existing",
            runId: null,
            sequenceNo: 4,
            entryKind: "final_answer",
            displayRole: "assistant",
            stepId: null,
            stepNumber: null,
            contentText: "Replacement answer",
            createdAt: "2026-01-01T00:01:02Z",
          },
        ],
        pagination: { hasMore: false, nextBeforeSequence: null },
      })

    render(<Home />)
    await screen.findAllByText("Project 1")
    await screen.findByText("Old prompt")
    await screen.findByText("Old answer")

    fireEvent.click(screen.getByRole("button", { name: "Edit user message" }))
    const editor = screen.getByDisplayValue("Old prompt")
    fireEvent.change(editor, { target: { value: "Updated prompt" } })
    fireEvent.click(screen.getByText("Send"))

    await waitFor(() => {
      expect(sidecarApiMocks.acpRequest).toHaveBeenCalledWith(
        "session/edit_and_prompt",
        expect.objectContaining({
          sessionId: "sess_existing",
          eventId: "evt_user_1",
        })
      )
    })

    expect(screen.getByText("Updated prompt")).toBeInTheDocument()
    expect(screen.queryByText("Old answer")).not.toBeInTheDocument()

    act(() => {
      sidecarApiMocks.emitNotification({
        method: "session/update",
        params: {
          sessionId: "sess_existing",
          update: {
            sessionUpdate: "agent_message_chunk",
            content: { type: "text", text: "Replacement" },
          },
        },
      })
    })

    expect(screen.getByText("Replacement")).toBeInTheDocument()

    await act(async () => {
      sidecarApiMocks.resolvePrompt()
    })

    await screen.findByText("Replacement answer")
  })
})
