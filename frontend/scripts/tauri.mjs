import { existsSync, readdirSync, statSync } from "node:fs"
import path from "node:path"
import process from "node:process"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, "..")
const repoRoot = path.resolve(frontendRoot, "..")
const tauriConfigPath = path.join(repoRoot, "src-tauri", "tauri.conf.json")

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: process.env,
  })

  if (result.error) {
    throw result.error
  }

  if (typeof result.status === "number") {
    process.exit(result.status)
  }

  process.exit(1)
}

function shouldInjectConfig(commandName) {
  return commandName === "dev" || commandName === "build"
}

function artifactLooksPrepared(filePath, minSizeBytes) {
  if (!existsSync(filePath)) {
    return false
  }

  return statSync(filePath).size >= minSizeBytes
}

function directoryHasEntries(dirPath) {
  if (!existsSync(dirPath)) {
    return false
  }

  return readdirSync(dirPath).length > 0
}

function ensureDesktopRuntimePreparedIfNeeded(commandName) {
  if (process.platform !== "darwin") {
    return
  }

  if (commandName !== "dev" && commandName !== "build") {
    return
  }

  if (process.arch !== "arm64") {
    return
  }

  const binaryPath = path.join(
    repoRoot,
    "src-tauri",
    "binaries",
    "nsbot-sidecar-aarch64-apple-darwin"
  )
  const fdPath = path.join(repoRoot, "src-tauri", "runtime", "search-tools", "fd")
  const rgPath = path.join(repoRoot, "src-tauri", "runtime", "search-tools", "rg")
  const templatesPath = path.join(repoRoot, "src-tauri", "runtime", "templates")

  const runtimeReady =
    artifactLooksPrepared(binaryPath, 1024 * 1024) &&
    artifactLooksPrepared(fdPath, 100 * 1024) &&
    artifactLooksPrepared(rgPath, 100 * 1024) &&
    directoryHasEntries(templatesPath)

  if (runtimeReady) {
    return
  }

  console.log("[tauri-wrapper] Preparing desktop runtime assets...")
  const result = spawnSync("bash", ["./scripts/prepare_desktop_runtime_macos.sh"], {
    cwd: repoRoot,
    stdio: "inherit",
    env: process.env,
  })

  if (result.error) {
    throw result.error
  }

  if (result.status !== 0) {
    process.exit(result.status ?? 1)
  }
}

function buildForwardedArgs(originalArgs) {
  if (originalArgs.length === 0) {
    return []
  }

  const [commandName, ...rest] = originalArgs
  if (!shouldInjectConfig(commandName)) {
    return originalArgs
  }

  const overrideConfig = {
    build: {
      beforeDevCommand: `npm --prefix ${frontendRoot} run dev -- --port 13000`,
      beforeBuildCommand: `npm --prefix ${frontendRoot} run build`,
    },
  }

  return [commandName, "--config", tauriConfigPath, "--config", JSON.stringify(overrideConfig), ...rest]
}

function resolveTauriCwd(commandName) {
  if (shouldInjectConfig(commandName)) {
    return repoRoot
  }

  return path.join(repoRoot, "src-tauri")
}

function main() {
  const originalArgs = process.argv.slice(2)
  const commandName = originalArgs[0]

  ensureDesktopRuntimePreparedIfNeeded(commandName)

  const tauriBin = path.join(
    frontendRoot,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "tauri.cmd" : "tauri"
  )

  run(tauriBin, buildForwardedArgs(originalArgs), resolveTauriCwd(commandName))
}

main()