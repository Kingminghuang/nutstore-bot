import { afterEach, describe, expect, it, vi } from "vitest"

describe("sidecar-client discovery refresh", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it("re-reads discovery after an unauthorized response", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ baseUrl: "http://127.0.0.1:8765", token: "stale-token" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Unauthorized" }), {
          status: 401,
          headers: { "content-type": "application/json" },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ baseUrl: "http://127.0.0.1:9999", token: "fresh-token" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )
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
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/sidecar/discovery", { cache: "no-store" })
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8765/provider-catalog",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer stale-token" }),
      })
    )
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/sidecar/discovery", { cache: "no-store" })
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "http://127.0.0.1:9999/provider-catalog",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer fresh-token" }),
      })
    )
  })

  it("re-reads discovery after a network failure", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ baseUrl: "http://127.0.0.1:8765", token: "token-a" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ baseUrl: "http://127.0.0.1:8766", token: "token-b" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ connections: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )

    vi.stubGlobal("fetch", fetchMock)

    const { getProviders } = await import("@/lib/sidecar-client")
    const response = await getProviders()

    expect(response).toEqual({ connections: [] })
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/sidecar/discovery", { cache: "no-store" })
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "http://127.0.0.1:8766/providers",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer token-b" }),
      })
    )
  })
})
