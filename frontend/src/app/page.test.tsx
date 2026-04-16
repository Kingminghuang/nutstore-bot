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
      if (method === "_nsbot/workspace/list") {
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
      if (method === "_nsbot/workspace/sessions/list") {
        return Promise.resolve({ sessions: [baseSession] })
      }
      if (method === "_nsbot/attachment/list") {
        return Promise.resolve({ attachments: [] })
      }
      if (method === "_nsbot/draft_attachment/list") {
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
      if (method === "_nsbot/session/edit_and_prompt") {
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
        providerId: "openai",
        models: [
          {
            connectionId: "prov_openai",
            providerLabel: "OpenAI",
            providerId: "openai",
            modelId: "gpt-5.4",
            label: "gpt-5.4",
            supportsReasoningTokens: true,
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
  searchWorkspaceEntries: vi.fn(async () => ({ entries: [] })),
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
      turnId: null,
      sequenceNo: Number(event.sequenceNo ?? 0),
      entryKind: event.eventType === "agent_thought_chunk" ? "thinking" : String((event as { entryKind?: unknown }).entryKind ?? "user_input"),
      displayRole: String((event as { displayRole?: unknown }).displayRole ?? "user"),
      stepId: null,
      stepNumber: null,
      contentText:
        (event.payload as { params?: { update?: { content?: { displayText?: string } } } } | undefined)?.params?.update?.content?.displayText ??
        (event.payload as { params?: { update?: { content?: { text?: string } } } } | undefined)?.params?.update?.content?.text ??
        String((event as { contentText?: unknown }).contentText ?? ""),
      editableText:
        (event.payload as { params?: { update?: { content?: { editableText?: string } } } } | undefined)?.params?.update?.content?.editableText ??
        ((event as { editableText?: unknown }).editableText != null
          ? String((event as { editableText?: unknown }).editableText)
          : undefined),
      displayBlocks: (event as { displayBlocks?: unknown }).displayBlocks,
      promptBlocks:
        (event.payload as { params?: { update?: { content?: { promptBlocks?: unknown } } } } | undefined)?.params?.update?.content?.promptBlocks ??
        (event as { promptBlocks?: unknown }).promptBlocks,
      createdAt: String(event.createdAt ?? "2026-01-01T00:00:00Z"),
    }))
  ),
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

beforeEach(async () => {
  const sidecarApi = await import("@/shared/api/sidecar")

  sidecarApiMocks.reset()
  vi.clearAllMocks()
  sidecarApiMocks.getSessionTimeline.mockReset()
  vi.mocked(sidecarApi.getProviderCatalog).mockResolvedValue({ providers: [] })
  vi.mocked(sidecarApi.getProviders).mockResolvedValue({ connections: [] })
  vi.mocked(sidecarApi.getModelOptions).mockResolvedValue({
    groups: [
      {
        connectionId: "prov_openai",
        providerLabel: "OpenAI",
        providerId: "openai",
        models: [
          {
            connectionId: "prov_openai",
            providerLabel: "OpenAI",
            providerId: "openai",
            modelId: "gpt-5.4",
            label: "gpt-5.4",
            supportsReasoningTokens: true,
            reasoningEffortValues: ["low", "medium"],
          },
        ],
      },
    ],
    defaultSelection: { connectionId: "prov_openai", modelId: "gpt-5.4" },
  })
  ;(window as Window & { __TAURI__?: object }).__TAURI__ = {}
})

describe("Home page ACP bootstrap", () => {
  it("saves providers without triggering post-save validation", async () => {
    const sidecarApi = await import("@/shared/api/sidecar")
    const providerCatalog = [
      {
        id: "openai",
        label: "OpenAI / Compatible",
        kind: "builtin" as const,
        runtimeProvider: "openai" as const,
        baseUrlPolicy: "optional" as const,
        models: [{ id: "gpt-5.4", supportsReasoningTokens: true }],
      },
    ]
    const savedConnection = {
      id: "prov_openai",
      kind: "builtin" as const,
      runtimeProvider: "openai" as const,
      catalogProviderId: "openai",
      displayName: "OpenAI / Compatible",
      baseUrl: null,
      apiKeyConfigured: true,
      preferredModelId: "gpt-5.4",
      enabledModelIds: ["gpt-5.4"],
      updatedAt: "2026-04-15T00:00:00Z",
      modelPolicy: "all_catalog" as const,
      customModels: [],
    }

    vi.mocked(sidecarApi.getProviderCatalog).mockResolvedValue({ providers: providerCatalog })
    vi.mocked(sidecarApi.getProviders)
      .mockResolvedValueOnce({ connections: [] })
      .mockResolvedValueOnce({ connections: [savedConnection] })
    vi.mocked(sidecarApi.getModelOptions)
      .mockResolvedValueOnce({ groups: [], defaultSelection: null })
      .mockResolvedValueOnce({
        groups: [
          {
            connectionId: "prov_openai",
            providerLabel: "OpenAI / Compatible",
            providerId: "openai",
            models: [
              {
                connectionId: "prov_openai",
                providerLabel: "OpenAI / Compatible",
                providerId: "openai",
                modelId: "gpt-5.4",
                label: "gpt-5.4",
                supportsReasoningTokens: true,
              },
            ],
          },
        ],
        defaultSelection: { connectionId: "prov_openai", modelId: "gpt-5.4" },
      })
    vi.mocked(sidecarApi.createProvider).mockResolvedValue(savedConnection)

    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)
    await screen.findAllByText("Project 1")

    fireEvent.click(screen.getByRole("button", { name: "Settings" }))
    await screen.findByText("Providers")

    fireEvent.click(screen.getByRole("button", { name: "+ Connect" }))
    await screen.findByText("OpenAI / Compatible Configuration")

    fireEvent.change(screen.getByLabelText("OpenAI / Compatible API key"), {
      target: { value: "sk-openai" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Connect provider" }))

    await waitFor(() => {
      expect(sidecarApi.createProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "builtin",
          apiKey: "sk-openai",
        })
      )
    })
  })

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
            turnId: null,
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
            eventId: "evt_answer_1",
            sessionId: "sess_existing",
            turnId: null,
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

  it("cancels the request and resolves permission as cancelled when the user chooses cancel request", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: "Cancel request" }))

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
            turnId: null,
            sequenceNo: 1,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "First prompt",
            createdAt: "2026-01-01T00:00:01Z",
          },
          {
            id: "entry_answer_1",
            sessionId: "sess_existing",
            turnId: null,
            sequenceNo: 2,
            entryKind: "final_answer",
            displayRole: "assistant",
            stepId: null,
            stepNumber: null,
            contentText: "First answer",
            createdAt: "2026-01-01T00:00:02Z",
          },
          {
            id: "entry_user_2",
            eventId: "evt_user_2",
            sessionId: "sess_existing",
            turnId: null,
            sequenceNo: 3,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Old prompt",
            editableText: "Old prompt",
            promptBlocks: [{ type: "text", text: "Old prompt" }],
            createdAt: "2026-01-01T00:01:01Z",
          },
          {
            id: "entry_answer_2",
            eventId: "evt_answer_2",
            sessionId: "sess_existing",
            turnId: null,
            sequenceNo: 4,
            entryKind: "final_answer",
            displayRole: "assistant",
            stepId: null,
            stepNumber: null,
            contentText: "Old answer",
            createdAt: "2026-01-01T00:01:02Z",
          },
        ],
        pagination: { hasMore: false, nextBeforeSequence: null },
      })
      .mockResolvedValueOnce({
        events: [
          {
            id: "entry_user_3",
            eventId: "evt_user_3",
            sessionId: "sess_existing",
            turnId: null,
            sequenceNo: 5,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Updated prompt",
            createdAt: "2026-01-01T00:01:01Z",
          },
          {
            id: "entry_answer_3",
            eventId: "evt_answer_3",
            sessionId: "sess_existing",
            turnId: null,
            sequenceNo: 6,
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
    await screen.findByText("First prompt")
    await screen.findByText("First answer")
    await screen.findByText("Old prompt")
    await screen.findByText("Old answer")

    fireEvent.click(screen.getByRole("button", { name: "Edit user message" }))
    const editor = screen.getByDisplayValue("Old prompt")
    fireEvent.change(editor, { target: { value: "Updated prompt" } })
    fireEvent.click(screen.getByText("Send"))

    await waitFor(() => {
      expect(sidecarApiMocks.acpRequest).toHaveBeenCalledWith(
        "_nsbot/session/edit_and_prompt",
        expect.objectContaining({
          sessionId: "sess_existing",
          eventId: "evt_user_2",
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

  it("sends composer attachments as resource blocks", async () => {
    const sidecarApi = await import("@/shared/api/sidecar")

    sidecarApiMocks.getSessionTimeline.mockResolvedValue({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })
    vi.mocked(sidecarApi.listAttachments).mockResolvedValue({
      attachments: [
        {
          id: "att_1",
          sessionId: "sess_existing",
          workspaceId: "ws_1",
          fileName: "notes.txt",
          mimeType: "text/plain",
          sizeBytes: 12,
          status: "uploaded",
          createdAt: "2026-01-01T00:00:00Z",
          updatedAt: "2026-01-01T00:00:00Z",
        },
      ],
    })

    render(<Home />)
    await screen.findAllByText("Project 1")
    await screen.findAllByText("notes.txt")

    fireEvent.change(screen.getByPlaceholderText("Ask for follow-up changes"), {
      target: { value: "Summarize the attachment" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() => {
      expect(sidecarApiMocks.acpRequest).toHaveBeenNthCalledWith(
        2,
        "session/prompt",
        expect.objectContaining({
          sessionId: "sess_existing",
          prompt: [
            { type: "text", text: "Summarize the attachment" },
            {
              type: "resource",
              resource: {
                uri: "attachment://session/att_1",
                mimeType: "text/plain",
                title: "notes.txt",
              },
            },
          ],
        })
      )
    })
  })

  it("sends selected mentions as resource_link prompt blocks", async () => {
    const sidecarApi = await import("@/shared/api/sidecar")

    sidecarApiMocks.getSessionTimeline.mockResolvedValue({
      events: [],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })
    vi.mocked(sidecarApi.searchWorkspaceEntries).mockResolvedValue({
      entries: [
        {
          name: "page.tsx",
          relativePath: "src/app/page.tsx",
          parentPath: "src/app",
          absolutePath: "/tmp/project/src/app/page.tsx",
          uri: "file:///tmp/project/src/app/page.tsx",
          entryType: "file",
        },
      ],
    })

    render(<Home />)
    await screen.findAllByText("Project 1")

    fireEvent.change(screen.getByPlaceholderText("Ask for follow-up changes"), {
      target: { value: "Inspect @pag", selectionStart: 12 },
    })

    await waitFor(() => {
      expect(sidecarApi.searchWorkspaceEntries).toHaveBeenCalledWith("ws_1", "pag", { limit: 8 })
    })

    fireEvent.click(await screen.findByText("page.tsx"))
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() => {
      expect(sidecarApiMocks.acpRequest).toHaveBeenNthCalledWith(
        2,
        "session/prompt",
        expect.objectContaining({
          sessionId: "sess_existing",
          prompt: expect.arrayContaining([
            { type: "text", text: "Inspect " },
            expect.objectContaining({
              type: "resource_link",
              uri: "file:///tmp/project/src/app/page.tsx",
              name: "page.tsx",
            }),
          ]),
        })
      )
    })
  })

  it("preserves attachment resource blocks during edit-and-rerun", async () => {
    sidecarApiMocks.getSessionTimeline
      .mockResolvedValueOnce({
        events: [
          {
            id: "entry_user_attachment",
            eventId: "evt_user_attachment",
            sessionId: "sess_existing",
            turnId: null,
            sequenceNo: 1,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Summarize this\nnotes.txt",
            editableText: "Summarize this",
            displayBlocks: [
              { type: "text", text: "Summarize this" },
              { type: "resource", label: "notes.txt", uri: "attachment://session/att_1" },
            ],
            promptBlocks: [
              { type: "text", text: "Summarize this" },
              {
                type: "resource",
                resource: {
                  uri: "attachment://session/att_1",
                  mimeType: "text/plain",
                  title: "notes.txt",
                },
              },
            ],
            createdAt: "2026-01-01T00:00:01Z",
          },
        ],
        pagination: { hasMore: false, nextBeforeSequence: null },
      })
      .mockResolvedValueOnce({
        events: [
          {
            id: "entry_user_attachment_2",
            eventId: "evt_user_attachment_2",
            sessionId: "sess_existing",
            turnId: null,
            sequenceNo: 2,
            entryKind: "user_input",
            displayRole: "user",
            stepId: null,
            stepNumber: null,
            contentText: "Explain in more detail\nnotes.txt",
            editableText: "Explain in more detail",
            displayBlocks: [
              { type: "text", text: "Explain in more detail" },
              { type: "resource", label: "notes.txt", uri: "attachment://session/att_1" },
            ],
            promptBlocks: [
              { type: "text", text: "Explain in more detail" },
              {
                type: "resource",
                resource: {
                  uri: "attachment://session/att_1",
                  mimeType: "text/plain",
                  title: "notes.txt",
                },
              },
            ],
            createdAt: "2026-01-01T00:00:02Z",
          },
        ],
        pagination: { hasMore: false, nextBeforeSequence: null },
      })

    render(<Home />)
    await screen.findAllByText("Project 1")
    await screen.findAllByText("notes.txt")

    fireEvent.click(screen.getByRole("button", { name: "Edit user message" }))
    const editor = screen.getByDisplayValue("Summarize this")
    fireEvent.change(editor, { target: { value: "Explain in more detail" } })
    fireEvent.click(screen.getByText("Send"))

    await waitFor(() => {
      expect(sidecarApiMocks.acpRequest).toHaveBeenCalledWith(
        "_nsbot/session/edit_and_prompt",
        expect.objectContaining({
          sessionId: "sess_existing",
          eventId: "evt_user_attachment",
          prompt: [
            { type: "text", text: "Explain in more detail" },
            {
              type: "resource",
              resource: {
                uri: "attachment://session/att_1",
                mimeType: "text/plain",
                title: "notes.txt",
              },
            },
          ],
        })
      )
    })
  })

  it("drops removed mention resource_link blocks during edit-and-rerun", async () => {
    sidecarApiMocks.getSessionTimeline.mockResolvedValueOnce({
      events: [
        {
          id: "entry_user_mention",
          eventId: "evt_user_mention",
          sessionId: "sess_existing",
          turnId: null,
          sequenceNo: 1,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "Inspect page.tsx",
          editableText: "Inspect ",
          displayBlocks: [
            { type: "text", text: "Inspect " },
            { type: "resource_link", label: "page.tsx", uri: "file:///tmp/project/src/app/page.tsx" },
          ],
          promptBlocks: [
            { type: "text", text: "Inspect " },
            {
              type: "resource_link",
              uri: "file:///tmp/project/src/app/page.tsx",
              name: "page.tsx",
            },
          ],
          createdAt: "2026-01-01T00:00:01Z",
        },
      ],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    render(<Home />)
    await screen.findAllByText("Project 1")
    await screen.findByText("page.tsx")

    fireEvent.click(screen.getByRole("button", { name: "Edit user message" }))
    const editor = screen.getByDisplayValue("Inspect @page.tsx")
    fireEvent.change(editor, { target: { value: "Inspect again" } })
    fireEvent.click(screen.getByText("Send"))

    await waitFor(() => {
      expect(sidecarApiMocks.acpRequest).toHaveBeenCalledWith(
        "_nsbot/session/edit_and_prompt",
        expect.objectContaining({
          sessionId: "sess_existing",
          eventId: "evt_user_mention",
          prompt: [{ type: "text", text: "Inspect again" }],
        })
      )
    })
  })
})
