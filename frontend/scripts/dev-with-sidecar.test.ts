import { afterEach, describe, expect, it, vi } from "vitest"
import path from "node:path"
import { fileURLToPath } from "node:url"

const killMock = vi.fn()
const { cpSyncMock, existsSyncMock, homedirMock, mkdirSyncMock } = vi.hoisted(() => ({
  cpSyncMock: vi.fn(),
  existsSyncMock: vi.fn(),
  homedirMock: vi.fn(() => "/home/tester"),
  mkdirSyncMock: vi.fn(),
}))

vi.mock("node:fs", () => ({
  cpSync: cpSyncMock,
  existsSync: existsSyncMock,
  mkdirSync: mkdirSyncMock,
  default: {
    cpSync: cpSyncMock,
    existsSync: existsSyncMock,
    mkdirSync: mkdirSyncMock,
  },
}))

vi.mock("node:os", () => ({
  default: {
    homedir: homedirMock,
  },
}))

const testScriptDir = path.dirname(fileURLToPath(import.meta.url))
const workspaceRoot = path.resolve(testScriptDir, "..", "..")
const sourceTemplatesDir = path.join(workspaceRoot, "templates")

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
    existsSyncMock.mockReset()
    mkdirSyncMock.mockReset()
    cpSyncMock.mockReset()
    homedirMock.mockReset()
    homedirMock.mockReturnValue("/home/tester")
  })

  it("starts sidecar and frontend dev processes on non-Windows", async () => {
    const sidecarChild = createChild()
    const frontendChild = createChild()
    const spawnImpl = vi.fn().mockReturnValueOnce(sidecarChild).mockReturnValueOnce(frontendChild)

    vi.stubGlobal("setTimeout", vi.fn(() => ({ unref: vi.fn() })))
    vi.spyOn(process, "exit").mockImplementation(vi.fn() as never)

    const targetTemplatesDir = path.join("/home/tester", ".nsbot", "templates")
    existsSyncMock.mockImplementation((targetPath: string) =>
      targetPath === sourceTemplatesDir || targetPath === targetTemplatesDir
    )

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
    expect(cpSyncMock).not.toHaveBeenCalled()
  })

  it("starts frontend through cmd on Windows", async () => {
    const sidecarChild = createChild()
    const frontendChild = createChild()
    const spawnImpl = vi.fn().mockReturnValueOnce(sidecarChild).mockReturnValueOnce(frontendChild)

    vi.stubGlobal("setTimeout", vi.fn(() => ({ unref: vi.fn() })))
    vi.spyOn(process, "exit").mockImplementation(vi.fn() as never)

    existsSyncMock.mockImplementation((targetPath: string) => targetPath === sourceTemplatesDir)

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
    expect(mkdirSyncMock).toHaveBeenCalledWith(path.join("/home/tester", ".nsbot"), { recursive: true })
    expect(cpSyncMock).toHaveBeenCalledWith(
      sourceTemplatesDir,
      path.join("/home/tester", ".nsbot", "templates"),
      { recursive: true }
    )
  })

  it("uses NS_BOT_HOME and does not overwrite existing templates", async () => {
    const sidecarChild = createChild()
    const frontendChild = createChild()
    const spawnImpl = vi.fn().mockReturnValueOnce(sidecarChild).mockReturnValueOnce(frontendChild)

    vi.stubGlobal("setTimeout", vi.fn(() => ({ unref: vi.fn() })))
    vi.spyOn(process, "exit").mockImplementation(vi.fn() as never)

    const customHome = "/tmp/custom-home"
    const targetTemplatesDir = path.join(customHome, "templates")
    existsSyncMock.mockImplementation((targetPath: string) =>
      targetPath === sourceTemplatesDir || targetPath === targetTemplatesDir
    )

    const { startDevWithSidecar } = await import("./dev-with-sidecar.mjs")
    startDevWithSidecar({
      platform: "linux",
      env: { NS_BOT_HOME: customHome },
      spawnImpl,
    })

    expect(mkdirSyncMock).not.toHaveBeenCalled()
    expect(cpSyncMock).not.toHaveBeenCalled()
  })
})
