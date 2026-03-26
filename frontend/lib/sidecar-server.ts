import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import path from "node:path"

import "server-only"

type DiscoveryFilePayload = {
  baseUrl: string
  token: string
}

export async function readSidecarDiscovery(): Promise<DiscoveryFilePayload> {
  const discoveryPath = resolveDiscoveryPath()
  const payload = JSON.parse(
    await readFile(discoveryPath, "utf-8")
  ) as DiscoveryFilePayload

  if (!payload.baseUrl || !payload.token) {
    throw new Error("Invalid sidecar discovery file")
  }

  return {
    baseUrl: payload.baseUrl,
    token: payload.token,
  }
}

export async function proxySidecarRequest(
  routePath: string,
  init?: RequestInit
): Promise<Response> {
  const discovery = await readSidecarDiscovery()
  const headers = new Headers(init?.headers)
  headers.set("Authorization", `Bearer ${discovery.token}`)

  return fetch(`${discovery.baseUrl}${routePath}`, {
    ...init,
    headers,
    cache: "no-store",
  })
}

function resolveDiscoveryPath(): string {
  const nsBotHome = process.env.NS_BOT_HOME
  if (nsBotHome) {
    return path.resolve(nsBotHome, "service.json")
  }

  if (process.platform === "darwin") {
    return path.join(
      homedir(),
      "Library",
      "Application Support",
      "NutstoreBot",
      "service.json"
    )
  }

  if (process.platform === "win32") {
    const appData = process.env.APPDATA
    if (appData) {
      return path.join(appData, "NutstoreBot", "service.json")
    }
  }

  const xdgStateHome = process.env.XDG_STATE_HOME
  if (xdgStateHome) {
    return path.join(xdgStateHome, "NutstoreBot", "service.json")
  }

  const xdgConfigHome = process.env.XDG_CONFIG_HOME
  if (xdgConfigHome) {
    return path.join(xdgConfigHome, "NutstoreBot", "service.json")
  }

  return path.join(homedir(), ".nsbot", "service.json")
}
