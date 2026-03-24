import { describe, expect, it, vi } from "vitest"

const readSidecarDiscoveryMock = vi.fn()

vi.mock("@/lib/sidecar-server", () => ({
  readSidecarDiscovery: readSidecarDiscoveryMock,
}))

describe("GET /api/sidecar/discovery", () => {
  it("returns discovery payload from sidecar-server", async () => {
    readSidecarDiscoveryMock.mockResolvedValueOnce({
      baseUrl: "http://127.0.0.1:8765",
      token: "route-token",
    })

    const { GET } = await import("@/app/api/sidecar/discovery/route")
    const response = await GET()

    expect(response.status).toBe(200)
    await expect(response.json()).resolves.toEqual({
      baseUrl: "http://127.0.0.1:8765",
      token: "route-token",
    })
  })
})
