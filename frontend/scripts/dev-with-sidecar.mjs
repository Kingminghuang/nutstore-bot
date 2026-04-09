import { spawn, spawnSync } from "node:child_process"
import { chmodSync, cpSync, existsSync, mkdirSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(scriptDir, "..")
const workspaceRoot = path.resolve(frontendDir, "..")
const sidecarDir = path.join(workspaceRoot, "sidecar")
const searchToolsCacheRoot = path.join(sidecarDir, "vendor", "search-tools")

function resolveUserPath(value, homeDir) {
  const trimmed = value.trim()
  if (trimmed === "~") {
    return homeDir
  }
  if (trimmed.startsWith("~/") || trimmed.startsWith("~\\")) {
    return path.resolve(homeDir, trimmed.slice(2))
  }
  return path.resolve(trimmed)
}

function resolveNsBotHome({ platform = process.platform, env = process.env, homeDir = os.homedir() } = {}) {
  const envOverride = env.NS_BOT_HOME
  if (envOverride && envOverride.trim() !== "") {
    return resolveUserPath(envOverride, homeDir)
  }

  if (platform === "win32") {
    if (env.APPDATA) {
      return path.resolve(env.APPDATA, "NutstoreBot")
    }
    return path.resolve(homeDir, ".nsbot")
  }

  if (platform === "darwin") {
    return path.resolve(homeDir, "Library", "Application Support", "NutstoreBot")
  }

  if (env.XDG_STATE_HOME) {
    return path.resolve(env.XDG_STATE_HOME, "NutstoreBot")
  }

  if (env.XDG_CONFIG_HOME) {
    return path.resolve(env.XDG_CONFIG_HOME, "NutstoreBot")
  }

  return path.resolve(homeDir, ".nsbot")
}

function ensureTemplatesInitialized({ platform = process.platform, env = process.env } = {}) {
  const sourceTemplatesDir = path.join(workspaceRoot, "templates")
  if (!existsSync(sourceTemplatesDir)) {
    return
  }

  const nsBotHome = resolveNsBotHome({ platform, env })
  const targetTemplatesDir = path.join(nsBotHome, "templates")
  if (existsSync(targetTemplatesDir)) {
    return
  }

  mkdirSync(nsBotHome, { recursive: true })
  cpSync(sourceTemplatesDir, targetTemplatesDir, { recursive: true })
}

function resolveTargetTriple({ platform = process.platform, arch = process.arch } = {}) {
  if (platform === "darwin") {
    if (arch === "arm64") {
      return "aarch64-apple-darwin"
    }
    if (arch === "x64") {
      return "x86_64-apple-darwin"
    }
  }

  if (platform === "win32" && arch === "x64") {
    return "x86_64-pc-windows-msvc"
  }

  throw new Error(`Unsupported platform/arch for vendored search tools: ${platform}/${arch}`)
}

function resolveBinaryName(toolName, platform = process.platform) {
  if (platform === "win32") {
    return `${toolName}.exe`
  }
  return toolName
}

function runPrepareSearchTools({
  targetTriple,
  env = process.env,
  spawnSyncImpl = spawnSync,
}) {
  const result = spawnSyncImpl(
    "uv",
    ["run", "python", "scripts/prepare_search_tools.py", "--target", targetTriple],
    {
      cwd: sidecarDir,
      env,
      encoding: "utf-8",
      stdio: "pipe",
    }
  )
  if (result.status === 0) {
    return
  }

  const detail = [result.stdout, result.stderr]
    .filter((item) => typeof item === "string" && item.trim() !== "")
    .join("\n")
    .trim()
  throw new Error(detail || "Failed to prepare vendored fd/rg binaries")
}

export function ensureSearchToolsInitialized({
  platform = process.platform,
  arch = process.arch,
  env = process.env,
  spawnSyncImpl = spawnSync,
} = {}) {
  const nsBotHome = resolveNsBotHome({ platform, env })
  const binDir = path.join(nsBotHome, "bin")
  mkdirSync(binDir, { recursive: true })

  const targetTriple = resolveTargetTriple({ platform, arch })
  const fdBinaryName = resolveBinaryName("fd", platform)
  const rgBinaryName = resolveBinaryName("rg", platform)
  const fdTargetPath = path.join(binDir, fdBinaryName)
  const rgTargetPath = path.join(binDir, rgBinaryName)

  const fdCachePath = path.join(searchToolsCacheRoot, targetTriple, "fd", fdBinaryName)
  const rgCachePath = path.join(searchToolsCacheRoot, targetTriple, "rg", rgBinaryName)

  const needFd = !existsSync(fdTargetPath)
  const needRg = !existsSync(rgTargetPath)

  if (needFd || needRg) {
    if (!existsSync(fdCachePath) || !existsSync(rgCachePath)) {
      runPrepareSearchTools({
        targetTriple,
        env,
        spawnSyncImpl,
      })
    }

    if (needFd) {
      cpSync(fdCachePath, fdTargetPath)
    }
    if (needRg) {
      cpSync(rgCachePath, rgTargetPath)
    }
  }

  if (platform !== "win32") {
    chmodSync(fdTargetPath, 0o755)
    chmodSync(rgTargetPath, 0o755)
  }

  return {
    fdExecutable: fdTargetPath,
    rgExecutable: rgTargetPath,
  }
}

export function startDevWithSidecar({
  platform = process.platform,
  arch = process.arch,
  env = process.env,
  spawnImpl = spawn,
  prepareSearchToolsImpl = ensureSearchToolsInitialized,
} = {}) {
  ensureTemplatesInitialized({ platform, env })
  const searchTools = prepareSearchToolsImpl({ platform, arch, env })
  const sidecarEnv = {
    ...env,
    NSBOT_FD_EXECUTABLE: searchTools.fdExecutable,
    NSBOT_RG_EXECUTABLE: searchTools.rgExecutable,
  }

  const children = []
  let shuttingDown = false

  const sidecar = spawnImpl("uv", ["run", "python", "-m", "nsbot_sidecar.api.api_server"], {
    cwd: sidecarDir,
    env: sidecarEnv,
    stdio: "inherit",
  })
  children.push(sidecar)

  const frontend =
    platform === "win32"
      ? spawnImpl(env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", "npm run dev"], {
          cwd: frontendDir,
          env,
          stdio: "inherit",
        })
      : spawnImpl("npm", ["run", "dev", "--", "--port", "13000"], {
          cwd: frontendDir,
          env,
          stdio: "inherit",
        })
  children.push(frontend)

  function terminate(code = 0) {
    if (shuttingDown) {
      return
    }
    shuttingDown = true

    for (const child of children) {
      if (child.exitCode == null && child.signalCode == null) {
        child.kill("SIGTERM")
      }
    }

    setTimeout(() => {
      for (const child of children) {
        if (child.exitCode == null && child.signalCode == null) {
          child.kill("SIGKILL")
        }
      }
      process.exit(code)
    }, 500).unref()
  }

  sidecar.on("exit", (code, signal) => {
    if (shuttingDown) {
      return
    }
    const exitCode = code ?? (signal ? 1 : 0)
    terminate(exitCode)
  })

  frontend.on("exit", (code, signal) => {
    if (shuttingDown) {
      return
    }
    const exitCode = code ?? (signal ? 1 : 0)
    terminate(exitCode)
  })

  process.on("SIGINT", () => terminate(0))
  process.on("SIGTERM", () => terminate(0))

  return { sidecar, frontend, terminate }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  startDevWithSidecar()
}
