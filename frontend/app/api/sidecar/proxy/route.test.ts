import { NextRequest } from "next/server"
import { afterEach, describe, expect, it, vi } from "vitest"

const proxySidecarRequestMock = vi.fn()

vi.mock("@/lib/sidecar-server", () => ({
  proxySidecarRequest: proxySidecarRequestMock,
}))

describe("sidecar proxy route", () => {
  afterEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
  })

  it("passes request body and content-type through the proxy helper", async () => {
    proxySidecarRequestMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    )

    const { POST } = await import("@/app/api/sidecar/proxy/route")
    const request = new NextRequest("http://localhost/api/sidecar/proxy?path=%2Fruns", {
      method: "POST",
      body: JSON.stringify({ input: "hello" }),
      headers: {
        "content-type": "application/json",
      },
    })

    const response = await POST(request)

    expect(proxySidecarRequestMock).toHaveBeenCalledWith(
      "/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input: "hello" }),
        headers: expect.any(Headers),
      })
    )
    expect(response.status).toBe(200)
    await expect(response.json()).resolves.toEqual({ ok: true })
  })
})
