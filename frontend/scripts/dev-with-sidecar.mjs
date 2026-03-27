import { spawn } from "node:child_process"
import { cpSync, existsSync, mkdirSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(scriptDir, "..")
const workspaceRoot = path.resolve(frontendDir, "..")
const sidecarDir = path.join(workspaceRoot, "sidecar")

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

export function startDevWithSidecar({
  platform = process.platform,
  env = process.env,
  spawnImpl = spawn,
} = {}) {
  ensureTemplatesInitialized({ platform, env })

  const children = []
  let shuttingDown = false

  const sidecar = spawnImpl("uv", ["run", "python", "api_server.py"], {
    cwd: sidecarDir,
    env,
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
      : spawnImpl("npm", ["run", "dev"], {
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
