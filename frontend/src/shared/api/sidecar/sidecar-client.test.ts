import { afterEach, describe, expect, it, vi } from "vitest"

const requestMock = vi.fn()

vi.mock("@/shared/api/sidecar/acp-client", () => ({
  acpClient: {
    request: requestMock,
  },
}))

describe("sidecar-client ACP resource requests", () => {
  afterEach(() => {
    requestMock.mockReset()
    vi.resetModules()
  })

  it("loads provider catalog via ACP provider/catalog", async () => {
    requestMock.mockResolvedValueOnce({ providers: [] })

    const { getProviderCatalog } = await import("@/shared/api/sidecar")
    const response = await getProviderCatalog()

    expect(response).toEqual({ providers: [] })
    expect(requestMock).toHaveBeenCalledWith("provider/catalog")
  })

  it("validates provider via ACP provider/validate", async () => {
    requestMock.mockResolvedValueOnce({
      ok: true,
      providerId: "prov_openai",
      modelId: "gpt-5.4",
      healthStatus: "connected",
      healthMessage: "Validation succeeded",
      lastValidatedAt: "2026-03-27T10:00:00Z",
    })

    const { validateProvider } = await import("@/shared/api/sidecar")
    const response = await validateProvider("prov_openai", { modelId: "gpt-5.4" })

    expect(response).toMatchObject({
      ok: true,
      providerId: "prov_openai",
      modelId: "gpt-5.4",
    })
    expect(requestMock).toHaveBeenCalledWith("provider/validate", {
      providerId: "prov_openai",
      modelId: "gpt-5.4",
    })
  })

  it("requests timeline through ACP timeline/list", async () => {
    requestMock.mockResolvedValueOnce({
      events: [
        {
          eventId: "acpevt_1",
          sequenceNo: 1,
          createdAt: "2026-04-14T10:00:00Z",
          payload: {
            params: {
              update: {
                sessionUpdate: "user_message_chunk",
                content: {
                  type: "text",
                  text: "hello",
                },
              },
            },
          },
        },
      ],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    const { getSessionTimeline } = await import("@/shared/api/sidecar")
    const response = await getSessionTimeline("sess_1", { limit: 20, beforeSequence: 100 })

    expect(response.events).toHaveLength(1)
    expect(response.events[0]).toMatchObject({
      eventId: "acpevt_1",
      sequenceNo: 1,
    })
    expect(requestMock).toHaveBeenCalledWith("timeline/list", {
      sessionId: "sess_1",
      limit: 20,
      beforeSequence: 100,
    })
  })
})
