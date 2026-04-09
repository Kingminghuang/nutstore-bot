import { afterEach, describe, expect, it, vi } from "vitest"
import path from "node:path"
import { fileURLToPath } from "node:url"

const killMock = vi.fn()
const { chmodSyncMock, cpSyncMock, existsSyncMock, homedirMock, mkdirSyncMock } = vi.hoisted(() => ({
  chmodSyncMock: vi.fn(),
  cpSyncMock: vi.fn(),
  existsSyncMock: vi.fn(),
  homedirMock: vi.fn(() => "/home/tester"),
  mkdirSyncMock: vi.fn(),
}))

vi.mock("node:fs", () => ({
  chmodSync: chmodSyncMock,
  cpSync: cpSyncMock,
  existsSync: existsSyncMock,
  mkdirSync: mkdirSyncMock,
  default: {
    chmodSync: chmodSyncMock,
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
    chmodSyncMock.mockReset()
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
    const prepareSearchToolsImpl = vi.fn(() => ({
      fdExecutable: "/tmp/nsbot/bin/fd",
      rgExecutable: "/tmp/nsbot/bin/rg",
    }))

    const { startDevWithSidecar } = await import("./dev-with-sidecar.mjs")
    startDevWithSidecar({
      platform: "linux",
      env: {},
      spawnImpl,
      prepareSearchToolsImpl,
    })

    expect(spawnImpl).toHaveBeenNthCalledWith(
      1,
      "uv",
      ["run", "python", "-m", "nsbot_sidecar.api.api_server"],
      expect.objectContaining({
        stdio: "inherit",
        env: expect.objectContaining({
          NSBOT_FD_EXECUTABLE: "/tmp/nsbot/bin/fd",
          NSBOT_RG_EXECUTABLE: "/tmp/nsbot/bin/rg",
        }),
      })
    )
    expect(spawnImpl).toHaveBeenNthCalledWith(
      2,
      "npm",
      ["run", "dev", "--", "--port", "13000"],
      expect.objectContaining({ stdio: "inherit" })
    )
    expect(prepareSearchToolsImpl).toHaveBeenCalledTimes(1)
    expect(cpSyncMock).not.toHaveBeenCalled()
  })

  it("starts frontend through cmd on Windows", async () => {
    const sidecarChild = createChild()
    const frontendChild = createChild()
    const spawnImpl = vi.fn().mockReturnValueOnce(sidecarChild).mockReturnValueOnce(frontendChild)

    vi.stubGlobal("setTimeout", vi.fn(() => ({ unref: vi.fn() })))
    vi.spyOn(process, "exit").mockImplementation(vi.fn() as never)

    existsSyncMock.mockImplementation((targetPath: string) => targetPath === sourceTemplatesDir)
    const prepareSearchToolsImpl = vi.fn(() => ({
      fdExecutable: "C:\\tmp\\NutstoreBot\\bin\\fd.exe",
      rgExecutable: "C:\\tmp\\NutstoreBot\\bin\\rg.exe",
    }))

    const { startDevWithSidecar } = await import("./dev-with-sidecar.mjs")
    startDevWithSidecar({
      platform: "win32",
      env: { ComSpec: "C:\\Windows\\System32\\cmd.exe" },
      spawnImpl,
      prepareSearchToolsImpl,
    })

    expect(spawnImpl).toHaveBeenNthCalledWith(
      1,
      "uv",
      ["run", "python", "-m", "nsbot_sidecar.api.api_server"],
      expect.objectContaining({
        stdio: "inherit",
        env: expect.objectContaining({
          NSBOT_FD_EXECUTABLE: "C:\\tmp\\NutstoreBot\\bin\\fd.exe",
          NSBOT_RG_EXECUTABLE: "C:\\tmp\\NutstoreBot\\bin\\rg.exe",
        }),
      })
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
    expect(prepareSearchToolsImpl).toHaveBeenCalledTimes(1)
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
    const prepareSearchToolsImpl = vi.fn(() => ({
      fdExecutable: "/tmp/custom-home/bin/fd",
      rgExecutable: "/tmp/custom-home/bin/rg",
    }))

    const { startDevWithSidecar } = await import("./dev-with-sidecar.mjs")
    startDevWithSidecar({
      platform: "linux",
      env: { NS_BOT_HOME: customHome },
      spawnImpl,
      prepareSearchToolsImpl,
    })

    expect(mkdirSyncMock).not.toHaveBeenCalled()
    expect(cpSyncMock).not.toHaveBeenCalled()
    expect(prepareSearchToolsImpl).toHaveBeenCalledTimes(1)
  })

  it("copies cached fd/rg into NS_BOT_HOME/bin", async () => {
    const { ensureSearchToolsInitialized } = await import("./dev-with-sidecar.mjs")

    const nsBotHome = "/tmp/custom-home"
    const fdTarget = path.join(nsBotHome, "bin", "fd")
    const rgTarget = path.join(nsBotHome, "bin", "rg")
    const fdCache = path.join(
      workspaceRoot,
      "sidecar",
      "vendor",
      "search-tools",
      "aarch64-apple-darwin",
      "fd",
      "fd"
    )
    const rgCache = path.join(
      workspaceRoot,
      "sidecar",
      "vendor",
      "search-tools",
      "aarch64-apple-darwin",
      "rg",
      "rg"
    )

    existsSyncMock.mockImplementation((targetPath: string) => {
      if (targetPath === fdTarget || targetPath === rgTarget) {
        return false
      }
      if (targetPath === fdCache || targetPath === rgCache) {
        return true
      }
      return false
    })

    const result = ensureSearchToolsInitialized({
      platform: "darwin",
      arch: "arm64",
      env: { NS_BOT_HOME: nsBotHome },
    })

    expect(mkdirSyncMock).toHaveBeenCalledWith(path.join(nsBotHome, "bin"), {
      recursive: true,
    })
    expect(cpSyncMock).toHaveBeenCalledWith(fdCache, fdTarget)
    expect(cpSyncMock).toHaveBeenCalledWith(rgCache, rgTarget)
    expect(result).toEqual({
      fdExecutable: fdTarget,
      rgExecutable: rgTarget,
    })
    expect(chmodSyncMock).toHaveBeenCalledWith(fdTarget, 0o755)
    expect(chmodSyncMock).toHaveBeenCalledWith(rgTarget, 0o755)
  })

  it("downloads vendored fd/rg when cache is missing", async () => {
    const { ensureSearchToolsInitialized } = await import("./dev-with-sidecar.mjs")
    const nsBotHome = "/tmp/custom-home"
    const fdTarget = path.join(nsBotHome, "bin", "fd")
    const rgTarget = path.join(nsBotHome, "bin", "rg")
    const fdCache = path.join(
      workspaceRoot,
      "sidecar",
      "vendor",
      "search-tools",
      "aarch64-apple-darwin",
      "fd",
      "fd"
    )
    const rgCache = path.join(
      workspaceRoot,
      "sidecar",
      "vendor",
      "search-tools",
      "aarch64-apple-darwin",
      "rg",
      "rg"
    )

    let cacheReady = false
    existsSyncMock.mockImplementation((targetPath: string) => {
      if (targetPath === fdTarget || targetPath === rgTarget) {
        return false
      }
      if (targetPath === fdCache || targetPath === rgCache) {
        return cacheReady
      }
      return false
    })

    const spawnSyncImpl = vi.fn(() => {
      cacheReady = true
      return {
        status: 0,
        stdout: "",
        stderr: "",
      }
    })

    ensureSearchToolsInitialized({
      platform: "darwin",
      arch: "arm64",
      env: { NS_BOT_HOME: nsBotHome },
      spawnSyncImpl: spawnSyncImpl as never,
    })

    expect(spawnSyncImpl).toHaveBeenCalledTimes(1)
    expect(cpSyncMock).toHaveBeenCalledWith(fdCache, fdTarget)
    expect(cpSyncMock).toHaveBeenCalledWith(rgCache, rgTarget)
  })
})
