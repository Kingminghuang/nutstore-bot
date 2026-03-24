import { afterEach, describe, expect, it, vi } from "vitest"

const spawnMock = vi.fn()
const killMock = vi.fn()

vi.mock("node:child_process", () => ({
  spawn: spawnMock,
  default: {
    spawn: spawnMock,
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
    vi.clearAllMocks()
  })

  it("starts sidecar and frontend dev processes", async () => {
    const sidecarChild = createChild()
    const frontendChild = createChild()
    spawnMock.mockReturnValueOnce(sidecarChild).mockReturnValueOnce(frontendChild)

    const exitMock = vi.fn()
    vi.stubGlobal("setTimeout", vi.fn(() => ({ unref: vi.fn() })))
    vi.spyOn(process, "exit").mockImplementation(exitMock as never)

    await import("./dev-with-sidecar.mjs")

    expect(spawnMock).toHaveBeenNthCalledWith(
      1,
      "uv",
      ["run", "python", "api_server.py"],
      expect.objectContaining({ stdio: "inherit" })
    )
    expect(spawnMock).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(/npm(\.cmd)?$/),
      ["run", "dev"],
      expect.objectContaining({ stdio: "inherit" })
    )
    expect(exitMock).not.toHaveBeenCalled()
  })
})
