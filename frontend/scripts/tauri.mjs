import { chmodSync, cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs"
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

function resolvePreparedSidecarPath() {
  if (process.platform === "darwin" && process.arch === "arm64") {
    return path.join(
      repoRoot,
      "src-tauri",
      "binaries",
      "nsbot-sidecar-aarch64-apple-darwin"
    )
  }

  return null
}

function syncTauriDevArtifactsIfNeeded(commandName) {
  if (commandName !== "dev") {
    return
  }

  const cleanupTargets = [path.join(frontendRoot, "dist")]
  const debugRoot = path.join(repoRoot, "src-tauri", "target", "debug")
  const runtimeSource = path.join(repoRoot, "src-tauri", "runtime")
  const runtimeTarget = path.join(debugRoot, "runtime")
  const preparedSidecarPath = resolvePreparedSidecarPath()
  const debugSidecarTarget = path.join(debugRoot, "nsbot-sidecar")

  let removedAny = false
  for (const targetPath of cleanupTargets) {
    if (!existsSync(targetPath)) {
      continue
    }

    rmSync(targetPath, { recursive: true, force: true })
    removedAny = true
    console.log(`[tauri-wrapper] Removed stale dev artifact: ${targetPath}`)
  }

  let refreshedAny = false
  if (existsSync(runtimeSource)) {
    mkdirSync(debugRoot, { recursive: true })
    rmSync(runtimeTarget, { recursive: true, force: true })
    cpSync(runtimeSource, runtimeTarget, { recursive: true })
    refreshedAny = true
    console.log(`[tauri-wrapper] Refreshed dev runtime: ${runtimeTarget}`)
  }

  if (preparedSidecarPath && existsSync(preparedSidecarPath)) {
    mkdirSync(debugRoot, { recursive: true })
    rmSync(debugSidecarTarget, { force: true })
    cpSync(preparedSidecarPath, debugSidecarTarget)
    chmodSync(debugSidecarTarget, 0o755)
    refreshedAny = true
    console.log(`[tauri-wrapper] Refreshed dev sidecar: ${debugSidecarTarget}`)
  }

  if (!removedAny && !refreshedAny) {
    console.log("[tauri-wrapper] No stale dev artifacts found.")
  }
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

  const forcePrepare = commandName === "dev"
  if (runtimeReady && !forcePrepare) {
    return
  }

  console.log(
    forcePrepare
      ? "[tauri-wrapper] Rebuilding desktop runtime assets for tauri dev..."
      : "[tauri-wrapper] Preparing desktop runtime assets..."
  )
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
  syncTauriDevArtifactsIfNeeded(commandName)

  const tauriBin = path.join(
    frontendRoot,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "tauri.cmd" : "tauri"
  )

  run(tauriBin, buildForwardedArgs(originalArgs), resolveTauriCwd(commandName))
}

main()