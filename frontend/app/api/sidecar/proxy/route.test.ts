import { NextRequest } from "next/server"
import { afterEach, describe, expect, it, vi } from "vitest"

const proxyNSBotRequestMock = vi.fn()

vi.mock("@/lib/sidecar-server", () => ({
  proxyNSBotRequest: proxyNSBotRequestMock,
}))

describe("sidecar proxy route", () => {
  afterEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
  })

  it("passes request body and content-type through the proxy helper", async () => {
    proxyNSBotRequestMock.mockResolvedValueOnce(
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

    expect(proxyNSBotRequestMock).toHaveBeenCalledWith(
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

  it("forwards multipart uploads as binary payload", async () => {
    proxyNSBotRequestMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    )

    const { POST } = await import("@/app/api/sidecar/proxy/route")
    const formData = new FormData()
    formData.append("file", new Blob(["hello"]), "hello.txt")
    const request = new NextRequest(
      "http://localhost/api/sidecar/proxy?path=%2Fsessions%2Fsess_1%2Fattachments",
      {
        method: "POST",
        body: formData,
      }
    )

    await POST(request)

    const init = proxyNSBotRequestMock.mock.calls[0][1] as RequestInit
    expect(init.body).toBeInstanceOf(ArrayBuffer)
  })
})
