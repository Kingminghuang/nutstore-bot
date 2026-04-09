import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

import Home from "@/app/page"

const sidecarClientMocks = vi.hoisted(() => ({
  getSessionTimeline: vi.fn(),
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
  let decodedPath = decodedPathMatch ? decodeURIComponent(decodedPathMatch[1]) : ""
  if (decodedPath === "") {
    try {
      decodedPath = new URL(url).pathname
    } catch {
      decodedPath = ""
    }
  }
  const body =
    typeof init?.body === "string"
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : null
  if (decodedPath === "/workspaces" && init?.method === "POST") {
    const nextWorkspaceId = `ws_${workspaceSeed}`
    workspaceSeed += 1
    const createdWorkspace = {
      id: nextWorkspaceId,
      name: String(body?.name ?? "new-workspace"),
      pathLabel: String(body?.pathLabel ?? body?.realPath ?? "/tmp/new-workspace"),
      realPath: String(body?.realPath ?? "/tmp/new-workspace"),
      createdAt: "2026-03-24T12:00:00Z",
      updatedAt: "2026-03-24T12:00:00Z",
    }
    workspacesFixture = [...workspacesFixture, createdWorkspace]
    workspaceIndexStatusById[nextWorkspaceId] = "indexed"
    return new Response(JSON.stringify(createdWorkspace), { status: 200 })
  }

  if (decodedPath === "/workspaces" && (init?.method == null || init?.method === "GET")) {
    return new Response(
      JSON.stringify({
        workspaces: workspacesFixture,
      }),
      { status: 200 }
    )
  }

  const workspaceSessionsMatch = decodedPath.match(/^\/workspaces\/([^/]+)\/sessions$/)
  if (workspaceSessionsMatch && init?.method !== "POST") {
    const workspaceId = workspaceSessionsMatch[1]
    if (workspaceId === "ws_1") {
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
      if (includeEmptySession && !deletedSessionIds.has("sess_2")) {
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
      return new Response(JSON.stringify({ sessions }), { status: 200 })
    }
    return new Response(JSON.stringify({ sessions: [] }), { status: 200 })
  }

  const workspaceIndexStatusMatch = decodedPath.match(
    /^\/workspaces\/([^/]+)\/sidecar-index\/status$/
  )
  if (workspaceIndexStatusMatch && (init?.method == null || init?.method === "GET")) {
    const workspaceId = workspaceIndexStatusMatch[1]
    const status = workspaceIndexStatusById[workspaceId] ?? "not_started"
    return new Response(
      JSON.stringify({
        workspaceId,
        status,
        lastIndexedAt: status === "indexed" ? "2026-03-24T12:00:01Z" : null,
        stats: { scanned: 0, converted: 0, skipped: 0, failed: 0 },
        sourceCount: 0,
      }),
      { status: 200 }
    )
  }

  if (
    decodedPath === "/sessions/sess_1/timeline/msg_1/edit-and-run" &&
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
        entries: [
          {
            id: "msg_1",
            sessionId: "sess_1",
            runId: "run_edited_1",
            entryKind: "user_input",
            displayRole: "user",
            contentText: String(body?.content ?? "edited"),
            stepId: null,
            stepNumber: null,
            sequenceNo: 1,
            createdAt: "2026-03-24T12:19:00Z",
          },
          {
            id: "msg_edited_2",
            sessionId: "sess_1",
            runId: "run_edited_1",
            entryKind: "final_answer",
            displayRole: "assistant",
            contentText: "Edited run completed",
            stepId: null,
            stepNumber: null,
            sequenceNo: 2,
            createdAt: "2026-03-24T12:20:00Z",
          },
        ],
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

  if (decodedPath === "/runs" && init?.method === "POST") {
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
        entries: [
          {
            id: "msg_1",
            sessionId: responseSessionId,
            runId: "run_1",
            entryKind: "user_input",
            displayRole: "user",
            contentText: "Please wire the sidecar",
            stepId: null,
            stepNumber: null,
            sequenceNo: 1,
            createdAt: "2026-03-24T12:00:00Z",
          },
          {
            id: "msg_2",
            sessionId: responseSessionId,
            runId: "run_1",
            entryKind: "system_notice",
            displayRole: "system",
            contentText: `Run failed: ${runFailureReason}`,
            stepId: null,
            stepNumber: null,
            sequenceNo: 2,
            createdAt: "2026-03-24T12:12:00Z",
            contentJson: { noticeCode: "failed" },
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
        entries: useStreamingRun
          ? [
              {
                id: "msg_stream_user",
                sessionId: responseSessionId,
                runId: "run_1",
                entryKind: "user_input",
                displayRole: "user",
                contentText: "Run through sidecar",
                stepId: null,
                stepNumber: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:11:00Z",
              },
            ]
          : [
              {
                id: "msg_1",
                sessionId: responseSessionId,
                runId: null,
                entryKind: "user_input",
                displayRole: "user",
                contentText: "Please wire the sidecar",
                stepId: null,
                stepNumber: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:00:00Z",
              },
              {
                id: "msg_2",
                sessionId: responseSessionId,
                runId: "run_1",
                entryKind: "final_answer",
                displayRole: "assistant",
                contentText: "Run completed through sidecar.",
                stepId: null,
                stepNumber: null,
                sequenceNo: 2,
                createdAt: "2026-03-24T12:12:00Z",
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

  if (decodedPath === "/workspaces/ws_1" && init?.method === "PATCH") {
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

  if (decodedPath === "/workspaces/ws_1" && init?.method === "DELETE") {
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

vi.mock("@/shared/api/sidecar", () => ({
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
  getSessionTimeline: sidecarClientMocks.getSessionTimeline,
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
let workspaceSeed = 2
let workspacesFixture: Array<{
  id: string
  name: string
  pathLabel: string
  realPath: string
  createdAt: string
  updatedAt: string
}> = []
let workspaceIndexStatusById: Record<string, "indexed" | "not_started" | "disabled"> = {}

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
    workspaceSeed = 2
    workspacesFixture = [
      {
        id: "ws_1",
        name: "nutstore-bot",
        pathLabel: "/tmp/nutstore-bot",
        realPath: "/tmp/nutstore-bot",
        createdAt: "2026-03-24T12:00:00Z",
        updatedAt: "2026-03-24T12:00:00Z",
      },
    ]
    workspaceIndexStatusById = { ws_1: "not_started" }
    MockEventSource.instances = []
    fetchMock.mockClear()
    sidecarClientMocks.getSessionTimeline.mockReset()
    sidecarClientMocks.getSessionTimeline.mockImplementation(async (sessionId: string, options?: { limit?: number; beforeSequence?: number | null }) => {
      if (deletedSessionIds.has(sessionId)) {
        throw new Error("Session not found")
      }

      if (sessionId === "sess_2") {
        emptySessionMessagesFetchCount += 1
        if (failFirstEmptySessionMessagesRequest && emptySessionMessagesFetchCount === 1) {
          throw new Error("Temporary fetch failure")
        }
        return {
          entries: [],
          pagination: {
            hasMore: false,
            nextBeforeSequence: null,
          },
        }
      }

      if (usePaginatedHistory && options?.beforeSequence === 2) {
        return {
          entries: [
            {
              id: "msg_older_1",
              sessionId: "sess_1",
              runId: null,
              entryKind: "user_input",
              displayRole: "user",
              contentText: "Please wire the sidecar",
              stepId: null,
              stepNumber: null,
              sequenceNo: 1,
              createdAt: "2026-03-24T12:00:00Z",
            },
          ],
          pagination: {
            hasMore: false,
            nextBeforeSequence: null,
          },
        }
      }

      if (usePaginatedHistory) {
        return {
          entries: [
            {
              id: "msg_latest_1",
              sessionId: "sess_1",
              runId: null,
              entryKind: "final_answer",
              displayRole: "assistant",
              contentText: "I split provider catalog from persisted connections",
              stepId: null,
              stepNumber: null,
              sequenceNo: 2,
              createdAt: "2026-03-24T12:10:00Z",
            },
          ],
          pagination: {
            hasMore: true,
            nextBeforeSequence: 2,
          },
        }
      }

      return {
        entries: forceRunFailure
          ? [
              {
                id: "msg_1",
                sessionId: "sess_1",
                runId: "run_1",
                entryKind: "user_input",
                displayRole: "user",
                contentText: "Please wire the sidecar",
                stepId: null,
                stepNumber: null,
                sequenceNo: 1,
                createdAt: "2026-03-24T12:00:00Z",
              },
              {
                id: "msg_2",
                sessionId: "sess_1",
                runId: "run_1",
                entryKind: "system_notice",
                displayRole: "system",
                contentText: `Run failed: ${runFailureReason}`,
                stepId: null,
                stepNumber: null,
                sequenceNo: 2,
                createdAt: "2026-03-24T12:12:00Z",
                contentJson: { noticeCode: "failed" },
              },
            ]
          : runCompleted
            ? [
                {
                  id: "msg_1",
                  sessionId: "sess_1",
                  runId: "run_1",
                  entryKind: "user_input",
                  displayRole: "user",
                  contentText: "Please wire the sidecar",
                  stepId: null,
                  stepNumber: null,
                  sequenceNo: 1,
                  createdAt: "2026-03-24T12:00:00Z",
                },
                {
                  id: "msg_2",
                  sessionId: "sess_1",
                  runId: "run_1",
                  entryKind: "final_answer",
                  displayRole: "assistant",
                  contentText: "Run completed through sidecar.",
                  stepId: null,
                  stepNumber: null,
                  sequenceNo: 2,
                  createdAt: "2026-03-24T12:12:00Z",
                },
              ]
            : useStreamingRun
              ? [
                  {
                    id: "msg_stream_user",
                    sessionId: "sess_1",
                    runId: "run_1",
                    entryKind: "user_input",
                    displayRole: "user",
                    contentText: "Run through sidecar",
                    stepId: null,
                    stepNumber: null,
                    sequenceNo: 1,
                    createdAt: "2026-03-24T12:11:00Z",
                  },
                ]
              : [
                  {
                    id: "msg_1",
                    sessionId: "sess_1",
                    runId: null,
                    entryKind: "user_input",
                    displayRole: "user",
                    contentText: "Please wire the sidecar",
                    stepId: null,
                    stepNumber: null,
                    sequenceNo: 1,
                    createdAt: "2026-03-24T12:00:00Z",
                  },
                  {
                    id: "msg_2",
                    sessionId: "sess_1",
                    runId: null,
                    entryKind: "final_answer",
                    displayRole: "assistant",
                    contentText: "I split provider catalog from persisted connections",
                    stepId: null,
                    stepNumber: null,
                    sequenceNo: 2,
                    createdAt: "2026-03-24T12:10:00Z",
                  },
                ],
        pagination: {
          hasMore: false,
          nextBeforeSequence: null,
        },
      }
    })
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

  it("polls sidecar index status after adding a workspace", async () => {
    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("OpenAI - gpt-5.4-mini")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText("Add a new directory"))
    fireEvent.change(screen.getByLabelText("Directory name"), {
      target: { value: "new-workspace" },
    })
    fireEvent.change(screen.getByLabelText("Directory path"), {
      target: { value: "/tmp/new-workspace" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add directory" }))

    await waitFor(() => {
      const indexStatusCalls = fetchMock.mock.calls.filter(([input, init]) => {
        const value = String(input)
        return (
          value.includes("/workspaces/ws_2/sidecar-index/status") &&
          (init?.method == null || init?.method === "GET")
        )
      })
      expect(indexStatusCalls.length).toBeGreaterThan(0)
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
        expect.stringContaining("/sessions/sess_1/timeline/msg_1/edit-and-run"),
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

  it("loads and renders persisted timeline entries for session history", async () => {
    runCompleted = true
    sidecarClientMocks.getSessionTimeline.mockResolvedValueOnce({
      entries: [
        {
          id: "msg_1",
          sessionId: "sess_1",
          runId: "run_1",
          sequenceNo: 1,
          entryKind: "user_input",
          displayRole: "user",
          stepId: null,
          stepNumber: null,
          contentText: "Please wire the sidecar",
          createdAt: "2026-03-24T12:00:00Z",
        },
        {
          id: "rstep_1",
          runId: "run_1",
          sessionId: "sess_1",
          sequenceNo: 1,
          entryKind: "planning",
          displayRole: "assistant",
          stepId: "step-1",
          stepNumber: null,
          contentText: "Inspect the workspace and identify the provider flow.",
          createdAt: "2026-03-24T12:00:01Z",
        },
        {
          id: "rstep_2",
          runId: "run_1",
          sessionId: "sess_1",
          sequenceNo: 2,
          entryKind: "action",
          displayRole: "assistant",
          stepId: "step-2",
          stepNumber: 1,
          contentText: null,
          contentJson: {
            thought: "Inspect workspace provider flow.",
            toolCalls: [],
            observations: ["Execution logs:", "Run completed through sidecar."],
            codeAction: 'print("Run completed through sidecar.")',
            actionOutput: "Run completed through sidecar.",
            error: null,
            usage: { inputTokens: 14, outputTokens: 6, reasoningTokens: 0 },
            durationMs: 180,
          },
          createdAt: "2026-03-24T12:00:02Z",
        },
      ],
      pagination: {
        hasMore: false,
        nextBeforeSequence: null,
      },
    })

    render(<Home />)

    await waitFor(() => {
      expect(screen.getByText("Planning step")).toBeInTheDocument()
    })

    expect(screen.getByText("Inspect the workspace and identify the provider flow.")).toBeInTheDocument()
    expect(screen.getByText("Step 1")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Code action/i }))
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
        "run.status",
        {
          type: "run.status",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 1,
          createdAt: "2026-03-24T12:10:59Z",
          status: "running",
          message: "Run in progress",
        },
        "run_1:0"
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId("pre-step-run-loading")).toBeInTheDocument()
    })

    act(() => {
      MockEventSource.instances[0].emit(
        "run.timeline-entry",
        {
          type: "run.timeline-entry",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 2,
          createdAt: "2026-03-24T12:11:00Z",
          entry: {
            id: "entry_action_running",
            sessionId: "sess_1",
            runId: "run_1",
            sequenceNo: 2,
            entryKind: "action",
            displayRole: "assistant",
            stepId: "step-1",
            stepNumber: 1,
            contentText: null,
            createdAt: "2026-03-24T12:11:00Z",
            contentJson: {
              thought: "Inspect workspace",
              toolCalls: [],
              observations: ["Execution logs:", "running"],
              codeAction: 'print(\"running\")',
              actionOutput: null,
              error: null,
              usage: { inputTokens: 12, outputTokens: 4, reasoningTokens: 0 },
              durationMs: 180,
            },
          },
        },
        "run_1:3"
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Running...")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("pre-step-run-loading")).not.toBeInTheDocument()

    act(() => {
      MockEventSource.instances[0].emit(
        "run.timeline-entry",
        {
          type: "run.timeline-entry",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 3,
          createdAt: "2026-03-24T12:11:00Z",
          entry: {
            id: "msg_stream_assistant",
            sessionId: "sess_1",
            runId: "run_1",
            sequenceNo: 2,
            entryKind: "final_answer",
            displayRole: "assistant",
            stepId: null,
            stepNumber: null,
            contentText: "Run completed through sidecar.",
            createdAt: "2026-03-24T12:12:00Z",
          },
        },
        "run_1:1"
      )
      MockEventSource.instances[0].emit(
        "run.completed",
        {
          type: "run.completed",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 4,
          createdAt: "2026-03-24T12:12:00Z",
          finalAnswer: "Run completed through sidecar.",
        },
        "run_1:4"
      )
      MockEventSource.instances[0].emit(
        "run.replay-ready",
        {
          type: "run.replay-ready",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 5,
          createdAt: "2026-03-24T12:12:01Z",
          lastEventSequence: 4,
        },
        "run_1:5"
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Run completed through sidecar.")).toBeInTheDocument()
    })
    expect(screen.queryByText("Running...")).not.toBeInTheDocument()
    expect(screen.queryByTestId("pre-step-run-loading")).not.toBeInTheDocument()

    expect(workspaceSessionsFetchCount).toBe(2)
  })

  it("scopes loading indicators to the active run id across multiple runs in one session", async () => {
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
        "run.status",
        {
          type: "run.status",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 1,
          createdAt: "2026-03-24T12:10:59Z",
          status: "running",
          message: "Run in progress",
        },
        "run_1:0"
      )
      MockEventSource.instances[0].emit(
        "run.timeline-entry",
        {
          type: "run.timeline-entry",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 2,
          createdAt: "2026-03-24T12:11:00Z",
          entry: {
            id: "entry_action_run_1",
            sessionId: "sess_1",
            runId: "run_1",
            sequenceNo: 2,
            entryKind: "action",
            displayRole: "assistant",
            stepId: "step-1",
            stepNumber: 1,
            contentText: null,
            createdAt: "2026-03-24T12:11:00Z",
            contentJson: {
              thought: "Inspect workspace",
              toolCalls: [],
              observations: ["Execution logs:", "run 1"],
              codeAction: 'print(\"run 1\")',
              actionOutput: null,
              error: null,
              usage: { inputTokens: 12, outputTokens: 4, reasoningTokens: 0 },
              durationMs: 180,
            },
          },
        },
        "run_1:1"
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Running...")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("pre-step-run-loading")).not.toBeInTheDocument()

    act(() => {
      MockEventSource.instances[0].emit(
        "run.completed",
        {
          type: "run.completed",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 3,
          createdAt: "2026-03-24T12:12:00Z",
          finalAnswer: "Run 1 completed",
        },
        "run_1:2"
      )
    })

    await waitFor(() => {
      expect(screen.queryByText("Running...")).not.toBeInTheDocument()
    })

    act(() => {
      MockEventSource.instances[0].emit(
        "run.status",
        {
          type: "run.status",
          runId: "run_2",
          sessionId: "sess_1",
          sequence: 4,
          createdAt: "2026-03-24T12:12:10Z",
          status: "running",
          message: "Run in progress",
        },
        "run_2:0"
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId("pre-step-run-loading")).toBeInTheDocument()
    })
    expect(screen.queryByText("Running...")).not.toBeInTheDocument()

    act(() => {
      MockEventSource.instances[0].emit(
        "run.timeline-entry",
        {
          type: "run.timeline-entry",
          runId: "run_2",
          sessionId: "sess_1",
          sequence: 5,
          createdAt: "2026-03-24T12:12:11Z",
          entry: {
            id: "entry_action_run_2",
            sessionId: "sess_1",
            runId: "run_2",
            sequenceNo: 3,
            entryKind: "action",
            displayRole: "assistant",
            stepId: "step-2",
            stepNumber: 2,
            contentText: null,
            createdAt: "2026-03-24T12:12:11Z",
            contentJson: {
              thought: "Check updated status",
              toolCalls: [],
              observations: ["Execution logs:", "run 2"],
              codeAction: 'print(\"run 2\")',
              actionOutput: null,
              error: null,
              usage: { inputTokens: 10, outputTokens: 3, reasoningTokens: 0 },
              durationMs: 120,
            },
          },
        },
        "run_2:1"
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Running...")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("pre-step-run-loading")).not.toBeInTheDocument()
    expect(screen.getAllByText("Running...")).toHaveLength(1)
  })

  it("does not clear active run indicators when an older run terminal event arrives out of order", async () => {
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
        "run.status",
        {
          type: "run.status",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 1,
          createdAt: "2026-03-24T12:10:59Z",
          status: "running",
          message: "Run in progress",
        },
        "run_1:0"
      )
      MockEventSource.instances[0].emit(
        "run.timeline-entry",
        {
          type: "run.timeline-entry",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 2,
          createdAt: "2026-03-24T12:11:00Z",
          entry: {
            id: "entry_action_old",
            sessionId: "sess_1",
            runId: "run_1",
            sequenceNo: 2,
            entryKind: "action",
            displayRole: "assistant",
            stepId: "step-1",
            stepNumber: 1,
            contentText: null,
            createdAt: "2026-03-24T12:11:00Z",
            contentJson: {
              thought: "old run thought",
              toolCalls: [],
              observations: ["Execution logs:", "run 1"],
              codeAction: 'print(\"run 1\")',
              actionOutput: null,
              error: null,
              usage: { inputTokens: 12, outputTokens: 4, reasoningTokens: 0 },
              durationMs: 180,
            },
          },
        },
        "run_1:1"
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Running...")).toBeInTheDocument()
    })

    act(() => {
      MockEventSource.instances[0].emit(
        "run.status",
        {
          type: "run.status",
          runId: "run_2",
          sessionId: "sess_1",
          sequence: 3,
          createdAt: "2026-03-24T12:12:10Z",
          status: "running",
          message: "Run in progress",
        },
        "run_2:0"
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId("pre-step-run-loading")).toBeInTheDocument()
    })
    expect(screen.queryByText("Running...")).not.toBeInTheDocument()

    act(() => {
      MockEventSource.instances[0].emit(
        "run.completed",
        {
          type: "run.completed",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 4,
          createdAt: "2026-03-24T12:12:11Z",
          finalAnswer: "old run done",
        },
        "run_1:2"
      )
    })

    expect(screen.getByTestId("pre-step-run-loading")).toBeInTheDocument()
    expect(screen.queryByText("Running...")).not.toBeInTheDocument()

    act(() => {
      MockEventSource.instances[0].emit(
        "run.timeline-entry",
        {
          type: "run.timeline-entry",
          runId: "run_2",
          sessionId: "sess_1",
          sequence: 5,
          createdAt: "2026-03-24T12:12:12Z",
          entry: {
            id: "entry_action_new",
            sessionId: "sess_1",
            runId: "run_2",
            sequenceNo: 3,
            entryKind: "action",
            displayRole: "assistant",
            stepId: "step-2",
            stepNumber: 2,
            contentText: null,
            createdAt: "2026-03-24T12:12:12Z",
            contentJson: {
              thought: "new run thought",
              toolCalls: [],
              observations: ["Execution logs:", "run 2"],
              codeAction: 'print(\"run 2\")',
              actionOutput: null,
              error: null,
              usage: { inputTokens: 10, outputTokens: 3, reasoningTokens: 0 },
              durationMs: 120,
            },
          },
        },
        "run_2:1"
      )
    })

    await waitFor(() => {
      expect(screen.getByText("Running...")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("pre-step-run-loading")).not.toBeInTheDocument()
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

    fireEvent.click(screen.getByLabelText("Reasoning effort"))
    fireEvent.click(screen.getByRole("button", { name: "high" }))
    fireEvent.change(screen.getByPlaceholderText("Ask for follow-up changes"), {
      target: { value: "Use a more detailed chain of thought" },
    })
    fireEvent.click(screen.getByLabelText("Send"))

    await waitFor(() => {
      const runCalls = fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).includes("/runs") &&
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
          String(input).includes("/workspaces/ws_1/sessions") &&
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
          String(input).includes("/workspaces/ws_1/draft-attachments") &&
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

    expect(sidecarClientMocks.getSessionTimeline).toHaveBeenCalledWith("sess_1", { limit: 50 })

    fireEvent.click(screen.getByRole("button", { name: "Load earlier messages" }))

    await waitFor(() => {
      expect(screen.getByText("Please wire the sidecar")).toBeInTheDocument()
    })

    expect(sidecarClientMocks.getSessionTimeline).toHaveBeenCalledWith("sess_1", {
      limit: 50,
      beforeSequence: 2,
    })
  })
})
