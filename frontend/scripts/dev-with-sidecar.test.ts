import { afterEach, describe, expect, it, vi } from "vitest"

const killMock = vi.fn()

vi.mock("node:child_process", () => ({
  spawn: vi.fn(),
  default: {
    spawn: vi.fn(),
  },
}))

function createChild() {
  const listeners = new Map<string, ((code?: number | null, signal?: NodeJS.Signals | null) => void)[]>()
  return {
    exitCode: null as number | null,
    signalCode: null as NodeJS.Signals | null,
    kill: killMock,
    on(event: string, handler: (code?: number | null, signal?: NodeJS.Signals | null) => void) {
      const items = listeners.get(event) ?? []
      items.push(handler)
      listeners.set(event, items)
      return this
    },
    emit(event: string, code?: number | null, signal?: NodeJS.Signals | null) {
      for (const handler of listeners.get(event) ?? []) {
        handler(code, signal)
      }
    },
  }
}

describe("dev-with-sidecar script", () => {
  afterEach(() => {
    vi.resetModules()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })

  it("starts sidecar and frontend dev processes on non-Windows", async () => {
    const sidecarChild = createChild()
    const frontendChild = createChild()
    const spawnImpl = vi.fn().mockReturnValueOnce(sidecarChild).mockReturnValueOnce(frontendChild)

    vi.stubGlobal("setTimeout", vi.fn(() => ({ unref: vi.fn() })))
    vi.spyOn(process, "exit").mockImplementation(vi.fn() as never)

    const { startDevWithSidecar } = await import("./dev-with-sidecar.mjs")
    startDevWithSidecar({ platform: "linux", env: {}, spawnImpl })

    expect(spawnImpl).toHaveBeenNthCalledWith(
      1,
      "uv",
      ["run", "python", "api_server.py"],
      expect.objectContaining({ stdio: "inherit" })
    )
    expect(spawnImpl).toHaveBeenNthCalledWith(
      2,
      "npm",
      ["run", "dev"],
      expect.objectContaining({ stdio: "inherit" })
    )
  })

  it("starts frontend through cmd on Windows", async () => {
    const sidecarChild = createChild()
    const frontendChild = createChild()
    const spawnImpl = vi.fn().mockReturnValueOnce(sidecarChild).mockReturnValueOnce(frontendChild)

    vi.stubGlobal("setTimeout", vi.fn(() => ({ unref: vi.fn() })))
    vi.spyOn(process, "exit").mockImplementation(vi.fn() as never)

    const { startDevWithSidecar } = await import("./dev-with-sidecar.mjs")
    startDevWithSidecar({
      platform: "win32",
      env: { ComSpec: "C:\\Windows\\System32\\cmd.exe" },
      spawnImpl,
    })

    expect(spawnImpl).toHaveBeenNthCalledWith(
      1,
      "uv",
      ["run", "python", "api_server.py"],
      expect.objectContaining({ stdio: "inherit" })
    )
    expect(spawnImpl).toHaveBeenNthCalledWith(
      2,
      "C:\\Windows\\System32\\cmd.exe",
      ["/d", "/s", "/c", "npm run dev"],
      expect.objectContaining({ stdio: "inherit" })
    )
  })
})
