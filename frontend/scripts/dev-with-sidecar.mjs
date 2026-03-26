import { spawn } from "node:child_process"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(scriptDir, "..")
const workspaceRoot = path.resolve(frontendDir, "..")
const sidecarDir = path.join(workspaceRoot, "sidecar")

export function startDevWithSidecar({
  platform = process.platform,
  env = process.env,
  spawnImpl = spawn,
} = {}) {
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
