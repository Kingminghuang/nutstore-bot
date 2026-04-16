import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(scriptDir, "..")
const repoRoot = path.resolve(frontendDir, "..")
const tauriConfigPath = path.join(repoRoot, "src-tauri", "tauri.conf.json")

function resolveUserPath(value, homeDir) {
  const trimmed = value.trim()
  if (trimmed === "~") return homeDir
  if (trimmed.startsWith("~/") || trimmed.startsWith("~\\")) {
    return path.resolve(homeDir, trimmed.slice(2))
  }
  return path.resolve(trimmed)
}

function loadTauriIdentifier() {
  if (!existsSync(tauriConfigPath)) {
    return null
  }

  try {
    const raw = JSON.parse(readFileSync(tauriConfigPath, "utf-8"))
    return typeof raw.identifier === "string" && raw.identifier.trim() !== ""
      ? raw.identifier.trim()
      : null
  } catch {
    return null
  }
}

function resolveNsBotHome({ platform = process.platform, env = process.env, homeDir = os.homedir() } = {}) {
  const envOverride = env.NS_BOT_HOME
  if (envOverride && envOverride.trim() !== "") {
    return resolveUserPath(envOverride, homeDir)
  }

  if (platform === "win32") {
    if (env.APPDATA) return path.resolve(env.APPDATA, "NutstoreBot")
    return path.resolve(homeDir, ".nsbot")
  }

  if (platform === "darwin") {
    const tauriIdentifier = loadTauriIdentifier()
    if (tauriIdentifier) {
      return path.resolve(
        homeDir,
        "Library",
        "Application Support",
        tauriIdentifier,
        "NutstoreBot"
      )
    }
    return path.resolve(homeDir, "Library", "Application Support", "NutstoreBot")
  }

  if (env.XDG_STATE_HOME) return path.resolve(env.XDG_STATE_HOME, "NutstoreBot")
  if (env.XDG_CONFIG_HOME) return path.resolve(env.XDG_CONFIG_HOME, "NutstoreBot")
  return path.resolve(homeDir, ".nsbot")
}

function loadSidecarClientConfig() {
  const nsBotHome = resolveNsBotHome()
  const configPath = path.join(nsBotHome, "sidecar-client.json")
  if (!existsSync(configPath)) {
    return {
      baseUrl: "http://127.0.0.1:18765",
      authHeaderValue: "Bearer dev-token",
    }
  }

  const raw = JSON.parse(readFileSync(configPath, "utf-8"))
  return {
    baseUrl: String(raw.baseUrl || "http://127.0.0.1:18765").replace(/\/$/, ""),
    authHeaderValue: String(raw.authHeaderValue || "Bearer dev-token"),
  }
}

function writeGenerated(config) {
  const generatedDir = path.join(frontendDir, "src", "generated")
  mkdirSync(generatedDir, { recursive: true })
  const outputPath = path.join(generatedDir, "sidecar-config.ts")
  const content = `export const sidecarClientConfig = ${JSON.stringify(config, null, 2)}\n`
  writeFileSync(outputPath, content, "utf-8")
  return outputPath
}

const output = writeGenerated(loadSidecarClientConfig())
console.log(`Generated ${output}`)
