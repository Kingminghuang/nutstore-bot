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
})
