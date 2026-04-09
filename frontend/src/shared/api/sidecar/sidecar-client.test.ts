import { afterEach, describe, expect, it, vi } from "vitest"

describe("sidecar-client direct requests", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it("requests provider catalog through sidecar transport", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ providers: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )

    vi.stubGlobal("fetch", fetchMock)

    const { getProviderCatalog } = await import("@/shared/api/sidecar")
    const response = await getProviderCatalog()

    expect(response).toEqual({ providers: [] })
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18765/provider-catalog",
      expect.objectContaining({
        headers: expect.any(Headers),
        cache: "no-store",
      })
    )
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = options.headers as Headers
    expect(headers.get("Content-Type")).toBe("application/json")
    expect(headers.get("Authorization")).toBe("Bearer dev-token")
  })

  it("throws NSBotClientError for non-ok responses", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Unauthorized" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      })
    )

    vi.stubGlobal("fetch", fetchMock)

    const { getProviders } = await import("@/shared/api/sidecar")
    await expect(getProviders()).rejects.toMatchObject({
      name: "NSBotClientError",
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

    const { createProvider } = await import("@/shared/api/sidecar")
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
      name: "NSBotClientError",
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

    const { getProviders } = await import("@/shared/api/sidecar")
    await expect(getProviders()).rejects.toMatchObject({
      name: "NSBotClientError",
      status: 400,
      message: 'invalid payload: {"apiKey":"[REDACTED]"}',
    })
  })

  it("posts provider validation through sidecar transport", async () => {
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

    const { validateProvider } = await import("@/shared/api/sidecar")
    const response = await validateProvider("prov_openai", { modelId: "gpt-5.4" })

    expect(response).toMatchObject({
      ok: true,
      providerId: "prov_openai",
      modelId: "gpt-5.4",
      healthStatus: "connected",
    })
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:18765/providers/prov_openai/validate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ modelId: "gpt-5.4" }),
        headers: expect.any(Headers),
        cache: "no-store",
      })
    )
  })
})
