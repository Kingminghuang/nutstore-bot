import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

import Home from "@/app/page"

const sidecarClientMocks = vi.hoisted(() => ({
  getRunSteps: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  validateProvider: vi.fn(),
  getProviders: vi.fn(),
  getModelOptions: vi.fn(),
}))

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  const decodedPathMatch = url.match(/[?&]path=([^&]+)/)
  const decodedPath = decodedPathMatch ? decodeURIComponent(decodedPathMatch[1]) : ""
  const body =
    typeof init?.body === "string"
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : null
  if (url.includes("path=%2Fworkspaces%2Fws_1%2Fsessions") && init?.method !== "POST") {
    workspaceSessionsFetchCount += 1
    const sessions = [
      {
        id: "sess_1",
        workspaceId: "ws_1",
        title: "Backend driven title",
        titleSource: runCompleted ? "model" : useStreamingRun ? "heuristic" : "model",
        createdAt: "2026-03-24T12:00:00Z",
        updatedAt: runCompleted ? "2026-03-24T12:12:00Z" : "2026-03-24T12:10:00Z",
        lastMessageAt: runCompleted ? "2026-03-24T12:12:00Z" : "2026-03-24T12:10:00Z",
        messageCount: runCompleted ? 3 : useStreamingRun ? 1 : 2,
        lastMessagePreview: runCompleted
          ? "Run completed through sidecar."
          : useStreamingRun
            ? "Run through sidecar"
            : "I split provider catalog from persisted connections",
        activeConnectionId: "prov_openai",
        activeModelId: "gpt-5.4-mini",
      },
    ].filter((session) => !deletedSessionIds.has(session.id))
    if (includeEmptySession) {
      if (!deletedSessionIds.has("sess_2")) {
        sessions.unshift({
        id: "sess_2",
        workspaceId: "ws_1",
        title: "Fresh session",
        titleSource: "placeholder",
        createdAt: "2026-03-24T12:30:00Z",
        updatedAt: "2026-03-24T12:30:00Z",
        lastMessageAt: null,
        messageCount: 0,
        lastMessagePreview: null,
        activeConnectionId: "prov_openai",
        activeModelId: "gpt-5.4-mini",
        })
      }
    }
    return new Response(
      JSON.stringify({
        sessions,
      }),
      { status: 200 }
    )
  }

  if (url.includes("/api/sidecar/proxy?path=%2Fworkspaces") && !url.includes("%2Fsessions")) {
    return new Response(
      JSON.stringify({
        workspaces: [
          {
            id: "ws_1",
            name: "nutstore-bot",
            pathLabel: "/tmp/nutstore-bot",
            realPath: "/tmp/nutstore-bot",
            createdAt: "2026-03-24T12:00:00Z",
            updatedAt: "2026-03-24T12:00:00Z",
          },
        ],
      }),
      { status: 200 }
    )
  }

  if (
    decodedPath === "/sessions/sess_1/messages/msg_1/edit-and-run" &&
    init?.method === "POST"
  ) {
    return new Response(
      JSON.stringify({
        run: {
          id: "run_edited_1",
          status: "completed",
          finalAnswer: "Edited run completed",
        },
        session: {
          id: "sess_1",
          workspaceId: "ws_1",
          title: "Backend driven title",
          titleSource: "model",
          createdAt: "2026-03-24T12:00:00Z",
          updatedAt: "2026-03-24T12:20:00Z",
          lastMessageAt: "2026-03-24T12:20:00Z",
          messageCount: 2,
          lastMessagePreview: "Edited run completed",
          activeConnectionId: "prov_openai",
          activeModelId: "gpt-5.4-mini",
        },
        messages: [
          {
            id: "msg_1",
            sessionId: "sess_1",
            runId: "run_edited_1",
            role: "user",
            content: String(body?.content ?? "edited"),
            stepId: null,
            sequenceNo: 1,
            createdAt: "2026-03-24T12:19:00Z",
            metadataJson: null,
          },
          {
            id: "msg_edited_2",
            sessionId: "sess_1",
            runId: "run_edited_1",
            role: "assistant",
            content: "Edited run completed",
            stepId: null,
            sequenceNo: 2,
            createdAt: "2026-03-24T12:20:00Z",
            metadataJson: null,
          },
        ],
      }),
      { status: 200 }
    )
  }

  if (decodedPath.startsWith("/sessions/sess_1/messages")) {
    if (deletedSessionIds.has("sess_1")) {
      return new Response(JSON.stringify({ detail: "Session not found" }), { status: 404 })
    }
    if (usePaginatedHistory && decodedPath.includes("beforeSequence=2")) {
      return new Response(
        JSON.stringify({
          messages: [
            {
              id: "msg_older_1",
              sessionId: "sess_1",
              runId: null,
              role: "user",
              content: "Please wire the sidecar",
              stepId: null,
              sequenceNo: 1,
              createdAt: "2026-03-24T12:00:00Z",
              metadataJson: null,
            },
          ],
          pagination: {
            hasMore: false,
            nextBeforeSequence: null,
          },
        }),
        { status: 200 }
      )
    }

    if (usePaginatedHistory) {
      return new Response(
        JSON.stringify({
          messages: [
            {
              id: "msg_latest_1",
              sessionId: "sess_1",
              runId: null,
              role: "assistant",
              content: "I split provider catalog from persisted connections",
              stepId: null,
              sequenceNo: 2,
              createdAt: "2026-03-24T12:10:00Z",
              metadataJson: null,
            },
          ],
          pagination: {
            hasMore: true,
            nextBeforeSequence: 2,
          },
        }),
        { status: 200 }
      )
    }

    return new Response(
      JSON.stringify({
        messages: forceRunFailure
          ? [
              {
                id: "msg_1",
                sessionId: "sess_1",
                runId: "run_1",
                role: "user",
                content: "Please wire the sidecar",
                stepId: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:00:00Z",
                metadataJson: null,
              },
              {
                id: "msg_2",
                sessionId: "sess_1",
                runId: "run_1",
                role: "system",
                content: `Run failed: ${runFailureReason}`,
                stepId: null,
                sequenceNo: 2,
                createdAt: "2026-03-24T12:12:00Z",
                metadataJson: null,
              },
            ]
          : runCompleted
          ? [
              {
                id: "msg_1",
                sessionId: "sess_1",
                runId: "run_1",
                role: "user",
                content: "Please wire the sidecar",
                stepId: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:00:00Z",
                metadataJson: null,
              },
              {
                id: "msg_2",
                sessionId: "sess_1",
                runId: "run_1",
                role: "assistant",
                content: "Run completed through sidecar.",
                stepId: null,
                sequenceNo: 2,
                createdAt: "2026-03-24T12:12:00Z",
                metadataJson: null,
              },
            ]
          : useStreamingRun
          ? [
              {
                id: "msg_stream_user",
                sessionId: "sess_1",
                runId: "run_1",
                role: "user",
                content: "Run through sidecar",
                stepId: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:11:00Z",
                metadataJson: null,
              },
            ]
          : [
              {
                id: "msg_1",
                sessionId: "sess_1",
                runId: null,
                role: "user",
                content: "Please wire the sidecar",
                stepId: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:00:00Z",
                metadataJson: null,
              },
            ],
      }),
      { status: 200 }
    )
  }

  if (decodedPath.startsWith("/sessions/sess_2/messages")) {
    if (deletedSessionIds.has("sess_2")) {
      return new Response(JSON.stringify({ detail: "Session not found" }), { status: 404 })
    }
    emptySessionMessagesFetchCount += 1
    if (failFirstEmptySessionMessagesRequest && emptySessionMessagesFetchCount === 1) {
      return new Response(JSON.stringify({ detail: "Temporary fetch failure" }), { status: 500 })
    }
    return new Response(
      JSON.stringify({
        messages: [],
        pagination: {
          hasMore: false,
          nextBeforeSequence: null,
        },
      }),
      { status: 200 }
    )
  }

  if (decodedPath.startsWith("/workspaces/ws_1/draft-attachments")) {
    if (init?.method === "POST") {
      return new Response(
        JSON.stringify({
          id: "draftatt_1",
          workspaceId: "ws_1",
          fileName: "draft-note.txt",
          mimeType: "text/plain",
          sizeBytes: 12,
          createdAt: "2026-03-24T12:31:00Z",
          updatedAt: "2026-03-24T12:31:00Z",
        }),
        { status: 200 }
      )
    }
    if (init?.method === "DELETE") {
      return new Response(null, { status: 204 })
    }
    return new Response(
      JSON.stringify({
        draftAttachments: [],
      }),
      { status: 200 }
    )
  }

  if (decodedPath.startsWith("/sessions/sess_1/attachments")) {
    if (deletedSessionIds.has("sess_1")) {
      return new Response(JSON.stringify({ detail: "Session not found" }), { status: 404 })
    }
    if (init?.method === "DELETE") {
      return new Response(null, { status: 204 })
    }
    return new Response(
      JSON.stringify({
        attachments: [],
      }),
      { status: 200 }
    )
  }

  if (decodedPath.startsWith("/sessions/sess_2/attachments")) {
    if (deletedSessionIds.has("sess_2")) {
      return new Response(JSON.stringify({ detail: "Session not found" }), { status: 404 })
    }
    if (init?.method === "DELETE") {
      return new Response(null, { status: 204 })
    }
    return new Response(
      JSON.stringify({
        attachments: [],
      }),
      { status: 200 }
    )
  }

  if (url.includes("/api/sidecar/proxy?path=%2Fruns") && init?.method === "POST") {
    const requestSessionId = typeof body?.sessionId === "string" ? String(body.sessionId) : null
    const responseSessionId = requestSessionId ?? "sess_2"
    if (requestSessionId == null) {
      includeEmptySession = true
    }
    if (forceRunFailure) {
      return new Response(JSON.stringify({
        detail: runFailureReason,
        run: {
          id: "run_1",
          status: "failed",
          finalAnswer: null,
          errorCode: "missing_api_key",
          errorMessage: runFailureReason,
        },
        session: {
          id: responseSessionId,
          workspaceId: "ws_1",
          title: requestSessionId ? "Backend driven title" : "Fresh session",
          titleSource: requestSessionId ? "model" : "heuristic",
          createdAt: "2026-03-24T12:00:00Z",
          updatedAt: "2026-03-24T12:12:00Z",
          lastMessageAt: "2026-03-24T12:12:00Z",
          messageCount: 2,
          lastMessagePreview: `Run failed: ${runFailureReason}`,
          activeConnectionId: "prov_openai",
          activeModelId: "gpt-5.4-mini",
        },
        messages: [
          {
            id: "msg_1",
            sessionId: responseSessionId,
            runId: "run_1",
            role: "user",
            content: "Please wire the sidecar",
            stepId: null,
            sequenceNo: 1,
            createdAt: "2026-03-24T12:00:00Z",
            metadataJson: null,
          },
          {
            id: "msg_2",
            sessionId: responseSessionId,
            runId: "run_1",
            role: "system",
            content: `Run failed: ${runFailureReason}`,
            stepId: null,
            sequenceNo: 2,
            createdAt: "2026-03-24T12:12:00Z",
            metadataJson: null,
          },
        ],
      }), {
        status: 400,
      })
    }

    runCompleted = true
    return new Response(
      JSON.stringify({
        run: {
          id: "run_1",
          status: useStreamingRun ? "queued" : "completed",
          finalAnswer: useStreamingRun ? null : "Run completed through sidecar.",
        },
        session: {
          id: responseSessionId,
          workspaceId: "ws_1",
          title: requestSessionId ? "Backend driven title" : "Fresh session",
          titleSource: useStreamingRun ? "heuristic" : requestSessionId ? "model" : "heuristic",
          createdAt: "2026-03-24T12:00:00Z",
          updatedAt: "2026-03-24T12:12:00Z",
          lastMessageAt: "2026-03-24T12:12:00Z",
          messageCount: useStreamingRun ? 1 : 3,
          lastMessagePreview: useStreamingRun
            ? "Run through sidecar"
            : "Run completed through sidecar.",
          activeConnectionId: "prov_openai",
          activeModelId: "gpt-5.4-mini",
        },
        messages: useStreamingRun
          ? [
              {
                id: "msg_stream_user",
                sessionId: responseSessionId,
                runId: "run_1",
                role: "user",
                content: "Run through sidecar",
                stepId: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:11:00Z",
                metadataJson: null,
              },
            ]
          : [
              {
                id: "msg_1",
                sessionId: responseSessionId,
                runId: null,
                role: "user",
                content: "Please wire the sidecar",
                stepId: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:00:00Z",
                metadataJson: null,
              },
              {
                id: "msg_2",
                sessionId: responseSessionId,
                runId: "run_1",
                role: "assistant",
                content: "Run completed through sidecar.",
                stepId: null,
                sequenceNo: 2,
                createdAt: "2026-03-24T12:12:00Z",
                metadataJson: null,
              },
            ],
      }),
      { status: 200 }
    )
  }

  if (decodedPath === "/sessions/sess_1" && init?.method === "DELETE") {
    deletedSessionIds.add("sess_1")
    return new Response(null, { status: 204 })
  }

  if (decodedPath === "/sessions/sess_2" && init?.method === "DELETE") {
    deletedSessionIds.add("sess_2")
    return new Response(null, { status: 204 })
  }

  if (url.includes("path=%2Fworkspaces%2Fws_1") && init?.method === "PATCH") {
    return new Response(
      JSON.stringify({
        id: "ws_1",
        name: "renamed-workspace",
        pathLabel: "/tmp/renamed",
        realPath: "/tmp/nutstore-bot",
        createdAt: "2026-03-24T12:00:00Z",
        updatedAt: "2026-03-24T12:05:00Z",
      }),
      { status: 200 }
    )
  }

  if (url.includes("path=%2Fworkspaces%2Fws_1") && init?.method === "DELETE") {
    return new Response(null, { status: 204 })
  }

  return new Response(JSON.stringify({ detail: `Unhandled request: ${url}` }), {
    status: 500,
  })
})

class MockEventSource {
  static instances: MockEventSource[] = []

  url: string
  listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>()
  onerror: ((event: Event) => void) | null = null
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const fn = listener as (event: MessageEvent<string>) => void
    const items = this.listeners.get(type) ?? []
    items.push(fn)
    this.listeners.set(type, items)
  }

  emit(type: string, data: Record<string, unknown>, lastEventId: string) {
    const event = {
      type,
      data: JSON.stringify(data),
      lastEventId,
    } as MessageEvent<string>
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }

  reset() {
    this.listeners.clear()
    this.close.mockReset()
  }
}

vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource)

Object.defineProperty(window, "showDirectoryPicker", {
  value: undefined,
  configurable: true,
})

vi.mock("@/lib/sidecar-client", () => ({
  getProviderCatalog: vi.fn(async () => ({
    providers: [
      {
        id: "openai",
        label: "OpenAI / Compatible",
        kind: "builtin",
        runtimeProvider: "openai",
        baseUrlPolicy: "optional",
        models: [
          {
            id: "gpt-5.4",
            supportsReasoningTokens: true,
            reasoningEffortValues: ["none", "low", "medium", "high", "xhigh"],
          },
          {
            id: "gpt-5.4-mini",
            supportsReasoningTokens: true,
            reasoningEffortValues: ["none", "low", "medium", "high"],
          },
        ],
      },
      {
        id: "gemini",
        label: "Gemini",
        kind: "builtin",
        runtimeProvider: "gemini",
        baseUrlPolicy: "hidden",
        models: [{ id: "gemini/gemini-2.5-pro", supportsReasoningTokens: true }],
      },
    ],
  })),
  getProviders: sidecarClientMocks.getProviders.mockImplementation(async () => ({
    connections: [
      {
        id: "prov_openai",
        kind: "builtin",
        runtimeProvider: "openai",
        catalogProviderId: "openai",
        displayName: "OpenAI",
        baseUrl: null,
        apiKeyConfigured: true,
        healthStatus: "connected",
        healthMessage: "Validation succeeded",
        lastValidatedAt: "2026-03-24T12:00:00Z",
        preferredModelId: "gpt-5.4-mini",
        enabledModelIds: ["gpt-5.4-mini"],
        updatedAt: "2026-03-24T12:00:00Z",
        modelPolicy: "restricted",
        customModels: [],
        headers: [],
      },
    ],
  })),
  getModelOptions: sidecarClientMocks.getModelOptions.mockImplementation(async () => ({
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
            modelId: "gpt-5.4-mini",
            label: "gpt-5.4-mini",
            supportsReasoningTokens: true,
            reasoningEffortValues: ["none", "low", "medium", "high"],
          },
        ],
      },
    ],
    defaultSelection: {
      connectionId: "prov_openai",
      modelId: "gpt-5.4-mini",
    },
  })),
  getRunSteps: sidecarClientMocks.getRunSteps,
  createProvider: sidecarClientMocks.createProvider,
  updateProvider: sidecarClientMocks.updateProvider,
  deleteProvider: sidecarClientMocks.deleteProvider,
  validateProvider: sidecarClientMocks.validateProvider,
}))

let runCompleted = false
let workspaceSessionsFetchCount = 0
let forceRunFailure = false
let runFailureReason = "Provider connection is missing an API key"
let useStreamingRun = false
let usePaginatedHistory = false
let includeEmptySession = false
let emptySessionMessagesFetchCount = 0
let failFirstEmptySessionMessagesRequest = false
let deletedSessionIds = new Set<string>()

global.fetch = fetchMock as typeof fetch

describe("Home page", () => {
  beforeEach(() => {
    runCompleted = false
    workspaceSessionsFetchCount = 0
    forceRunFailure = false
    runFailureReason = "Provider connection is missing an API key"
    useStreamingRun = false
    usePaginatedHistory = false
    includeEmptySession = false
    emptySessionMessagesFetchCount = 0
    failFirstEmptySessionMessagesRequest = false
    deletedSessionIds = new Set<string>()
    MockEventSource.instances = []
    fetchMock.mockClear()
    sidecarClientMocks.getRunSteps.mockReset()
    sidecarClientMocks.getRunSteps.mockResolvedValue({ steps: [] })
    sidecarClientMocks.createProvider.mockReset()
    sidecarClientMocks.createProvider.mockResolvedValue({ id: "prov_new" })
    sidecarClientMocks.updateProvider.mockReset()
    sidecarClientMocks.updateProvider.mockResolvedValue({ id: "prov_openai" })
    sidecarClientMocks.deleteProvider.mockReset()
    sidecarClientMocks.deleteProvider.mockResolvedValue(undefined)
    sidecarClientMocks.validateProvider.mockReset()
    sidecarClientMocks.validateProvider.mockResolvedValue({
      ok: true,
      providerId: "prov_openai",
      modelId: "gpt-5.4-mini",
      healthStatus: "connected",
      healthMessage: "Validation succeeded",
      lastValidatedAt: "2026-03-24T12:00:00Z",
    })
    sidecarClientMocks.getProviders.mockReset()
    sidecarClientMocks.getProviders.mockResolvedValue({
      connections: [
        {
          id: "prov_openai",
          kind: "builtin",
          runtimeProvider: "openai",
          catalogProviderId: "openai",
          displayName: "OpenAI",
          baseUrl: null,
          apiKeyConfigured: true,
          healthStatus: "connected",
          healthMessage: "Validation succeeded",
          lastValidatedAt: "2026-03-24T12:00:00Z",
          preferredModelId: "gpt-5.4-mini",
          enabledModelIds: ["gpt-5.4-mini"],
          updatedAt: "2026-03-24T12:00:00Z",
          modelPolicy: "restricted",
          customModels: [],
          headers: [],
        },
      ],
    })
    sidecarClientMocks.getModelOptions.mockReset()
    sidecarClientMocks.getModelOptions.mockResolvedValue({
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
              modelId: "gpt-5.4-mini",
              label: "gpt-5.4-mini",
              supportsReasoningTokens: true,
              reasoningEffortValues: ["none", "low", "medium", "high"],
            },
          ],
        },
      ],
      defaultSelection: {
        connectionId: "prov_openai",
        modelId: "gpt-5.4-mini",
      },
    })
  })

  it("applies the backend default model selection", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })
  })

  it("renders backend workspace session titles instead of placeholders", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getAllByText("Backend driven title")).toHaveLength(2)
    })
  })

  it("submits edited user message through edit-and-run endpoint", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("Please wire the sidecar")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "Edit user message" }))
    fireEvent.change(screen.getByDisplayValue("Please wire the sidecar"), {
      target: { value: "Please wire the sidecar with retries" },
    })
    fireEvent.click(screen.getByText("Send"))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("path=%2Fsessions%2Fsess_1%2Fmessages%2Fmsg_1%2Fedit-and-run"),
        expect.objectContaining({
          method: "POST",
        })
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Edited run completed")).toBeInTheDocument()
    })
  })

  it("validates a saved provider and refreshes state", async () => {
    sidecarClientMocks.getProviders
      .mockResolvedValueOnce({
        connections: [
          {
            id: "prov_openai",
            kind: "builtin",
            runtimeProvider: "openai",
            catalogProviderId: "openai",
            displayName: "OpenAI",
            baseUrl: null,
            apiKeyConfigured: true,
            healthStatus: "connected",
            healthMessage: "Validation succeeded",
            lastValidatedAt: "2026-03-24T12:00:00Z",
            preferredModelId: "gpt-5.4-mini",
            enabledModelIds: ["gpt-5.4-mini"],
            updatedAt: "2026-03-24T12:00:00Z",
            modelPolicy: "restricted",
            customModels: [],
            headers: [],
          },
        ],
      })
      .mockResolvedValueOnce({
        connections: [
          {
            id: "prov_openai",
            kind: "builtin",
            runtimeProvider: "openai",
            catalogProviderId: "openai",
            displayName: "OpenAI",
            baseUrl: null,
            apiKeyConfigured: true,
            healthStatus: "connected",
            healthMessage: "Validation succeeded",
            lastValidatedAt: "2026-03-27T10:00:00Z",
            preferredModelId: "gpt-5.4",
            enabledModelIds: ["gpt-5.4"],
            updatedAt: "2026-03-27T10:00:00Z",
            modelPolicy: "restricted",
            customModels: [],
            headers: [],
          },
        ],
      })

    sidecarClientMocks.getModelOptions
      .mockResolvedValueOnce({
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
                modelId: "gpt-5.4-mini",
                label: "gpt-5.4-mini",
                supportsReasoningTokens: true,
                reasoningEffortValues: ["none", "low", "medium", "high"],
              },
            ],
          },
        ],
        defaultSelection: {
          connectionId: "prov_openai",
          modelId: "gpt-5.4-mini",
        },
      })
      .mockResolvedValueOnce({
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
                reasoningEffortValues: ["none", "low", "medium", "high", "xhigh"],
              },
            ],
          },
        ],
        defaultSelection: {
          connectionId: "prov_openai",
          modelId: "gpt-5.4",
        },
      })

    sidecarClientMocks.updateProvider.mockResolvedValueOnce({ id: "prov_openai" })
    sidecarClientMocks.validateProvider.mockResolvedValueOnce({
      ok: true,
      providerId: "prov_openai",
      modelId: "gpt-5.4",
      healthStatus: "connected",
      healthMessage: "Validation succeeded",
      lastValidatedAt: "2026-03-27T10:00:00Z",
    })

    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Settings"))
    fireEvent.click(screen.getByRole("button", { name: "Edit" }))
    fireEvent.change(screen.getByPlaceholderText("Provider display name"), {
      target: { value: "OpenAI Updated" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }))

    await waitFor(() => {
      expect(sidecarClientMocks.updateProvider).toHaveBeenCalled()
      expect(sidecarClientMocks.validateProvider).toHaveBeenCalledWith("prov_openai", {
        modelId: "gpt-5.4-mini",
      })
    })

    await waitFor(() => {
      expect(sidecarClientMocks.getProviders).toHaveBeenCalledTimes(2)
      expect(sidecarClientMocks.getModelOptions).toHaveBeenCalledTimes(2)
    })
  })

  it("falls back when refreshed model options exclude not validated providers", async () => {
    sidecarClientMocks.getModelOptions
      .mockResolvedValueOnce({
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
                modelId: "gpt-5.4-mini",
                label: "gpt-5.4-mini",
                supportsReasoningTokens: true,
                reasoningEffortValues: ["none", "low", "medium", "high"],
              },
            ],
          },
        ],
        defaultSelection: {
          connectionId: "prov_openai",
          modelId: "gpt-5.4-mini",
        },
      })
      .mockResolvedValueOnce({
        groups: [],
        defaultSelection: null,
      })

    sidecarClientMocks.getProviders
      .mockResolvedValueOnce({
        connections: [
          {
            id: "prov_openai",
            kind: "builtin",
            runtimeProvider: "openai",
            catalogProviderId: "openai",
            displayName: "OpenAI",
            baseUrl: null,
            apiKeyConfigured: true,
            healthStatus: "connected",
            healthMessage: "Validation succeeded",
            lastValidatedAt: "2026-03-24T12:00:00Z",
            preferredModelId: "gpt-5.4-mini",
            enabledModelIds: ["gpt-5.4-mini"],
            updatedAt: "2026-03-24T12:00:00Z",
            modelPolicy: "restricted",
            customModels: [],
            headers: [],
          },
        ],
      })
      .mockResolvedValueOnce({
        connections: [
          {
            id: "prov_openai",
            kind: "builtin",
            runtimeProvider: "openai",
            catalogProviderId: "openai",
            displayName: "OpenAI",
            baseUrl: null,
            apiKeyConfigured: true,
            healthStatus: "unknown",
            healthMessage: null,
            lastValidatedAt: null,
            preferredModelId: "gpt-5.4-mini",
            enabledModelIds: ["gpt-5.4-mini"],
            updatedAt: "2026-03-27T10:00:00Z",
            modelPolicy: "restricted",
            customModels: [],
            headers: [],
          },
        ],
      })

    sidecarClientMocks.updateProvider.mockResolvedValueOnce({ id: "prov_openai" })
    sidecarClientMocks.validateProvider.mockResolvedValueOnce({
      ok: false,
      providerId: "prov_openai",
      modelId: "gpt-5.4-mini",
      errorMessage: "Provider validation failed",
      healthStatus: "unknown",
      healthMessage: null,
      lastValidatedAt: "2026-03-27T10:00:00Z",
    })

    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Settings"))
    fireEvent.click(screen.getByRole("button", { name: "Edit" }))
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }))

    await waitFor(() => {
      expect(screen.getByText("No configured providers")).toBeInTheDocument()
    })
  })

  it("loads and renders persisted run steps for session history", async () => {
    runCompleted = true
    sidecarClientMocks.getRunSteps.mockResolvedValueOnce({
      steps: [
        {
          id: "rstep_1",
          runId: "run_1",
          sessionId: "sess_1",
          sequenceNo: 1,
          stepId: "step-1",
          stepKind: "planning",
          stepNumber: null,
          plan: "Inspect the workspace and identify the provider flow.",
          usage: { inputTokens: 10, outputTokens: 4, reasoningTokens: 0 },
          durationMs: 120,
          hasDelta: true,
          createdAt: "2026-03-24T12:00:01Z",
        },
        {
          id: "rstep_2",
          runId: "run_1",
          sessionId: "sess_1",
          sequenceNo: 2,
          stepId: "step-2",
          stepKind: "action",
          stepNumber: 1,
          codeAction: 'print("Run completed through sidecar.")',
          actionOutput: "Run completed through sidecar.",
          observations: ["Execution logs:", "Run completed through sidecar."],
          error: null,
          usage: { inputTokens: 14, outputTokens: 6, reasoningTokens: 0 },
          durationMs: 180,
          hasDelta: true,
          createdAt: "2026-03-24T12:00:02Z",
        },
      ],
    })

    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("Planning step")).toBeInTheDocument()
    })

    expect(screen.getByText("Inspect the workspace and identify the provider flow.")).toBeInTheDocument()
    expect(screen.getByText("Step 1")).toBeInTheDocument()
    expect(screen.getByText('print("Run completed through sidecar.")')).toBeInTheDocument()
  })

  it("sends prompts through /runs and renders returned assistant output", async () => {
    useStreamingRun = true
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("Ask for follow-up changes"), {
      target: { value: "Run through sidecar" },
    })
    fireEvent.click(screen.getByLabelText("Send"))

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1)
    })

    act(() => {
      MockEventSource.instances[0].emit(
        "run.delta",
        {
          type: "run.delta",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 1,
          createdAt: "2026-03-24T12:11:00Z",
          stepId: "step-1",
          text: "Run completed through sidecar.",
        },
        "run_1:1"
      )
      MockEventSource.instances[0].emit(
        "run.completed",
        {
          type: "run.completed",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 2,
          createdAt: "2026-03-24T12:12:00Z",
          finalAnswer: "Run completed through sidecar.",
        },
        "run_1:2"
      )
      MockEventSource.instances[0].emit(
        "run.replay-ready",
        {
          type: "run.replay-ready",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 3,
          createdAt: "2026-03-24T12:12:01Z",
          lastEventSequence: 2,
        },
        "run_1:3"
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Run completed through sidecar.")).toBeInTheDocument()
    })

    expect(workspaceSessionsFetchCount).toBe(2)
  })

  it("surfaces run failures without replacing existing messages", async () => {
    forceRunFailure = true
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("Ask for follow-up changes"), {
      target: { value: "Run through sidecar" },
    })
    fireEvent.click(screen.getByLabelText("Send"))

    await waitFor(() => {
      expect(screen.getByText("Provider connection is missing an API key")).toBeInTheDocument()
    })

    expect(screen.getByText("Run failed: Provider connection is missing an API key")).toBeInTheDocument()
    expect(screen.queryByText("Run completed through sidecar.")).not.toBeInTheDocument()
    expect(screen.getByText("Please wire the sidecar")).toBeInTheDocument()
  })

  it("renders persisted failed-run system messages after a reload", async () => {
    forceRunFailure = true
    runFailureReason = "Upstream model request failed"

    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getByText("Run failed: Upstream model request failed")).toBeInTheDocument()
    })
  })

  it("sends the selected reasoning effort with the run request", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText("Reasoning effort"), {
      target: { value: "high" },
    })
    fireEvent.change(screen.getByPlaceholderText("Ask for follow-up changes"), {
      target: { value: "Use a more detailed chain of thought" },
    })
    fireEvent.click(screen.getByLabelText("Send"))

    await waitFor(() => {
      const runCalls = fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).includes("/api/sidecar/proxy?path=%2Fruns") &&
          init?.method === "POST"
      )
      expect(runCalls).toHaveLength(1)
      expect(JSON.parse(String(runCalls[0][1]?.body))).toMatchObject({
        reasoningEffort: "high",
      })
    })
  })

  it("does not persist a session when clicking New session before first message", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "New session" }))

    await waitFor(() => {
      const createSessionCalls = fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).includes("path=%2Fworkspaces%2Fws_1%2Fsessions") &&
          init?.method === "POST"
      )
      expect(createSessionCalls).toHaveLength(0)
    })
  })

  it("loads workspace draft attachments after entering draft session mode", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "New session" }))

    await waitFor(() => {
      const draftAttachmentCalls = fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).includes("path=%2Fworkspaces%2Fws_1%2Fdraft-attachments") &&
          (init?.method == null || init?.method === "GET")
      )
      expect(draftAttachmentCalls.length).toBeGreaterThan(0)
    })

    expect(screen.getByPlaceholderText("Ask for follow-up changes")).toBeInTheDocument()
  })

  it("deletes a non-active session without changing the active one", async () => {
    includeEmptySession = true
    render(<Home />)

    await waitFor(() => {
      expect(screen.getAllByText("Fresh session").length).toBeGreaterThan(0)
      expect(screen.getAllByText("Backend driven title").length).toBeGreaterThan(0)
    })

    fireEvent.click(screen.getByLabelText("Remove session Backend driven title"))
    fireEvent.click(screen.getByRole("button", { name: "Remove session" }))

    await waitFor(() => {
      expect(screen.queryByText("Backend driven title")).not.toBeInTheDocument()
      expect(screen.getAllByText("Fresh session").length).toBeGreaterThan(0)
    })
  })

  it("deletes the active session and falls back to draft mode when none remain", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getAllByText("Backend driven title").length).toBeGreaterThan(0)
    })

    fireEvent.click(screen.getByLabelText("Remove session Backend driven title"))
    fireEvent.click(screen.getByRole("button", { name: "Remove session" }))

    await waitFor(() => {
      expect(screen.queryByText("Backend driven title")).not.toBeInTheDocument()
      expect(screen.getByPlaceholderText("Ask for follow-up changes")).toBeInTheDocument()
    })
  })

  it("loads session messages lazily with pagination", async () => {
    usePaginatedHistory = true
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("I split provider catalog from persisted connections")).toBeInTheDocument()
    })

    const initialMessageCalls = fetchMock.mock.calls.filter(([input]) => {
      const value = String(input)
      return value.includes("path=%2Fsessions%2Fsess_1%2Fmessages%3Flimit%3D50")
    })
    expect(initialMessageCalls).toHaveLength(1)

    fireEvent.click(screen.getByRole("button", { name: "Load earlier messages" }))

    await waitFor(() => {
      expect(screen.getByText("Please wire the sidecar")).toBeInTheDocument()
    })

    const olderPageCalls = fetchMock.mock.calls.filter(([input]) => {
      const value = String(input)
      return value.includes(
        "path=%2Fsessions%2Fsess_1%2Fmessages%3Flimit%3D50%26beforeSequence%3D2"
      )
    })
    expect(olderPageCalls).toHaveLength(1)
  })
})
