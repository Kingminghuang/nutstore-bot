import { afterEach, describe, expect, it, vi } from "vitest"
import { mkdtemp, mkdir, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

describe("sidecar-server proxyNSBotRequest", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.resetModules()
    vi.unstubAllEnvs()
  })

  it("preserves content-type when incoming headers are a Headers instance", async () => {
    const homeDir = await mkdtemp(path.join(tmpdir(), "ns-bot-test-"))
    await mkdir(homeDir, { recursive: true })
    await writeFile(
      path.join(homeDir, "service.json"),
      JSON.stringify({
        baseUrl: "http://127.0.0.1:18765",
        token: "test-token",
      })
    )
    vi.stubEnv("NS_BOT_HOME", homeDir)

    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)

    const { proxyNSBotRequest } = await import("@/shared/api/sidecar/sidecar-server")
    await proxyNSBotRequest("/providers", {
      method: "POST",
      headers: new Headers({ "Content-Type": "application/json" }),
      body: "{}",
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(headers.get("content-type")).toBe("application/json")
    expect(headers.get("authorization")).toBe("Bearer test-token")
  })
})
