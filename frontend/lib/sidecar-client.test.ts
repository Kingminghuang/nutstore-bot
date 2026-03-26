import { afterEach, describe, expect, it, vi } from "vitest"

describe("sidecar-client proxy requests", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it("requests provider catalog through Next sidecar proxy", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ providers: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )

    vi.stubGlobal("fetch", fetchMock)

    const { getProviderCatalog } = await import("@/lib/sidecar-client")
    const response = await getProviderCatalog()

    expect(response).toEqual({ providers: [] })
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sidecar/proxy?path=%2Fprovider-catalog",
      expect.objectContaining({
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
        cache: "no-store",
      })
    )
  })

  it("throws SidecarClientError for non-ok responses", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Unauthorized" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      })
    )

    vi.stubGlobal("fetch", fetchMock)

    const { getProviders } = await import("@/lib/sidecar-client")
    await expect(getProviders()).rejects.toMatchObject({
      name: "SidecarClientError",
      status: 401,
      message: "Unauthorized",
    })
  })

  it("formats validation error arrays into readable messages", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: [
            {
              type: "dict_type",
              loc: ["body"],
              msg: "Input should be a valid dictionary",
              input: "{...}",
            },
          ],
        }),
        {
          status: 422,
          headers: { "content-type": "application/json" },
        }
      )
    )

    vi.stubGlobal("fetch", fetchMock)

    const { createProvider } = await import("@/lib/sidecar-client")
    await expect(
      createProvider({
        kind: "custom",
        customSlug: "minimax",
        displayName: "MiniMax",
        baseUrl: "https://api.minimaxi.com/v1",
        apiKey: "sk-test",
        preferredModelId: "MiniMax-M2.7-highspeed",
        customModels: [
          {
            modelId: "MiniMax-M2.7-highspeed",
            displayName: "MiniMax-M2.7-highspeed",
            enabled: true,
          },
        ],
        headers: [],
      })
    ).rejects.toMatchObject({
      name: "SidecarClientError",
      status: 422,
      message: "body: Input should be a valid dictionary",
    })
  })

  it("redacts sensitive fields in detail string responses", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: 'invalid payload: {"apiKey":"sk-secret-123"}',
        }),
        {
          status: 400,
          headers: { "content-type": "application/json" },
        }
      )
    )

    vi.stubGlobal("fetch", fetchMock)

    const { getProviders } = await import("@/lib/sidecar-client")
    await expect(getProviders()).rejects.toMatchObject({
      name: "SidecarClientError",
      status: 400,
      message: 'invalid payload: {"apiKey":"[REDACTED]"}',
    })
  })

  it("posts provider validation through Next sidecar proxy", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          providerId: "prov_openai",
          modelId: "gpt-5.4",
          healthStatus: "connected",
          healthMessage: "Validation succeeded",
          lastValidatedAt: "2026-03-27T10:00:00Z",
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        }
      )
    )

    vi.stubGlobal("fetch", fetchMock)

    const { validateProvider } = await import("@/lib/sidecar-client")
    const response = await validateProvider("prov_openai", { modelId: "gpt-5.4" })

    expect(response).toMatchObject({
      ok: true,
      providerId: "prov_openai",
      modelId: "gpt-5.4",
      healthStatus: "connected",
    })
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sidecar/proxy?path=%2Fproviders%2Fprov_openai%2Fvalidate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ modelId: "gpt-5.4" }),
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
        cache: "no-store",
      })
    )
  })
})
