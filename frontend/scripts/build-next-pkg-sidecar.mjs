import { existsSync, mkdirSync, rmSync, cpSync, writeFileSync, chmodSync, readFileSync } from "node:fs"
import path from "node:path"
import process from "node:process"
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, "..")
const repoRoot = path.resolve(frontendRoot, "..")
const tauriBinariesDir = path.join(repoRoot, "src-tauri", "binaries")

function parseTargetTriple() {
  const cliArg = process.argv.find((arg) => arg.startsWith("--target-triple="))
  if (cliArg) {
    return cliArg.slice("--target-triple=".length)
  }
  return process.env.TARGET_TRIPLE || "aarch64-apple-darwin"
}

function resolvePkgTarget(targetTriple) {
  switch (targetTriple) {
    case "aarch64-apple-darwin":
      return "node22-macos-arm64"
    case "x86_64-apple-darwin":
      return "node22-macos-x64"
    default:
      throw new Error(`Unsupported target triple for pkg sidecar build: ${targetTriple}`)
  }
}

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: process.env,
  })
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")}`)
  }
}

function ensureStandaloneExists() {
  const standaloneDir = path.join(frontendRoot, ".next", "standalone")
  const standaloneServer = path.join(standaloneDir, "server.js")
  if (!existsSync(standaloneServer)) {
    throw new Error(`Missing standalone server entry: ${standaloneServer}`)
  }
}

function stageStandaloneAssets(stageRoot) {
  const standaloneSource = path.join(frontendRoot, ".next", "standalone")
  const staticSource = path.join(frontendRoot, ".next", "static")
  const publicSource = path.join(frontendRoot, "public")

  rmSync(stageRoot, { recursive: true, force: true })
  mkdirSync(stageRoot, { recursive: true })

  const stagedStandalone = path.join(stageRoot, "standalone")
  cpSync(standaloneSource, stagedStandalone, { recursive: true })

  if (existsSync(staticSource)) {
    const staticTarget = path.join(stagedStandalone, ".next", "static")
    mkdirSync(path.dirname(staticTarget), { recursive: true })
    cpSync(staticSource, staticTarget, { recursive: true })
  }

  if (existsSync(publicSource)) {
    const publicTarget = path.join(stagedStandalone, "public")
    cpSync(publicSource, publicTarget, { recursive: true })
  }

  patchStandaloneServerForPkg(stagedStandalone)
}

function patchStandaloneServerForPkg(stagedStandalone) {
  const serverPath = path.join(stagedStandalone, "server.js")
  const source = readFileSync(serverPath, "utf8")
  const patched = source.replace(
    "process.chdir(__dirname)",
    "if (!process.pkg) process.chdir(__dirname)"
  )

  if (patched === source) {
    throw new Error(`Unable to patch standalone server for pkg runtime: ${serverPath}`)
  }

  writeFileSync(serverPath, patched, "utf8")
}

function createLauncher(stageRoot) {
  const launcherPath = path.join(stageRoot, "launcher.cjs")
  writeFileSync(
    launcherPath,
    [
      "const path = require('path')",
      "const fs = require('fs')",
      "const entry = path.join(__dirname, 'standalone', 'server.js')",
      "if (!fs.existsSync(entry)) {",
      "  throw new Error(`Missing bundled standalone server: ${entry}`)",
      "}",
      "require('./standalone/server.js')",
      "",
    ].join("\n"),
    "utf8"
  )
  return launcherPath
}

function createPkgManifest(stageRoot) {
  const manifestPath = path.join(stageRoot, "package.json")
  writeFileSync(
    manifestPath,
    JSON.stringify(
      {
        name: "nsbot-next-pkg-sidecar",
        private: true,
        bin: "launcher.cjs",
        pkg: {
          scripts: [
            "launcher.cjs",
            "standalone/**/*.js",
            "standalone/**/*.cjs",
          ],
          assets: [
            "standalone/**/*",
          ],
        },
      },
      null,
      2
    ),
    "utf8"
  )
  return manifestPath
}

function main() {
  const targetTriple = parseTargetTriple()
  const pkgTarget = resolvePkgTarget(targetTriple)
  ensureStandaloneExists()

  const stageRoot = path.join(frontendRoot, ".next", "pkg-sidecar")
  stageStandaloneAssets(stageRoot)
  createLauncher(stageRoot)
  const manifestPath = createPkgManifest(stageRoot)

  mkdirSync(tauriBinariesDir, { recursive: true })
  const outputPath = path.join(tauriBinariesDir, `next-sidecar-${targetTriple}`)
  rmSync(outputPath, { force: true })

  try {
    run(
      "npx",
      ["@yao-pkg/pkg", manifestPath, "--targets", pkgTarget, "--output", outputPath],
      frontendRoot
    )
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    throw new Error(
      [
        reason,
        `Node runtime: ${process.version}`,
        `Requested pkg target: ${pkgTarget}`,
        "Troubleshooting:",
        "- Verify target spelling and availability in @yao-pkg/pkg-fetch.",
        "- Ensure package installation and registry access are healthy.",
        "- Retry after clearing stale npx/npm cache if this is a transient fetch issue.",
      ].join("\n")
    )
  }
  chmodSync(outputPath, 0o755)

  console.log(`Built Next pkg sidecar: ${outputPath}`)
}

main()
