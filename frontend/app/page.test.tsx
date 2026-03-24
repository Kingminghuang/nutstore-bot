import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

import Home from "@/app/page"

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
          { id: "gpt-5.4", supportsReasoningTokens: true },
          { id: "gpt-5.4-mini", supportsReasoningTokens: true },
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
  getProviders: vi.fn(async () => ({
    connections: [
      {
        id: "prov_openai",
        kind: "builtin",
        runtimeProvider: "openai",
        catalogProviderId: "openai",
        displayName: "OpenAI",
        baseUrl: null,
        apiKeyConfigured: true,
        preferredModelId: "gpt-5.4-mini",
        enabledModelIds: ["gpt-5.4-mini"],
        updatedAt: "2026-03-24T12:00:00Z",
        modelPolicy: "restricted",
        customModels: [],
        headers: [],
      },
    ],
  })),
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
            modelId: "gpt-5.4-mini",
            label: "gpt-5.4-mini",
            supportsReasoningTokens: true,
          },
        ],
      },
    ],
    defaultSelection: {
      connectionId: "prov_openai",
      modelId: "gpt-5.4-mini",
    },
  })),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
}))

let runCompleted = false
let workspaceSessionsFetchCount = 0
let forceRunFailure = false
let runFailureReason = "Provider connection is missing an API key"
let useStreamingRun = false

global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  if (url.includes("path=%2Fworkspaces%2Fws_1%2Fsessions") && init?.method !== "POST") {
    workspaceSessionsFetchCount += 1
    return new Response(
      JSON.stringify({
        sessions: [
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
        ],
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

  if (url.includes("path=%2Fsessions%2Fsess_1%2Fmessages")) {
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

  if (url.includes("/api/sidecar/proxy?path=%2Fruns") && init?.method === "POST") {
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
          id: "sess_1",
          workspaceId: "ws_1",
          title: "Backend driven title",
          titleSource: "model",
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
          id: "sess_1",
          workspaceId: "ws_1",
          title: "Backend driven title",
          titleSource: useStreamingRun ? "heuristic" : "model",
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
            ],
      }),
      { status: 200 }
    )
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
}) as typeof fetch

describe("Home page", () => {
  beforeEach(() => {
    runCompleted = false
    workspaceSessionsFetchCount = 0
    forceRunFailure = false
    runFailureReason = "Provider connection is missing an API key"
    useStreamingRun = false
    MockEventSource.instances = []
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
})
