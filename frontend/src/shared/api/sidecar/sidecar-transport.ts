import { invoke } from "@tauri-apps/api/core"

export type SidecarClientConfig = {
  baseUrl: string
  authHeaderValue: string
}

let cachedConfig: SidecarClientConfig | null = null

export function isTauriRuntime(): boolean {
  if (typeof window === "undefined") {
    return false
  }
  const tauriWindow = window as Window & { __TAURI__?: unknown; __TAURI_INTERNALS__?: unknown }
  return Boolean(tauriWindow.__TAURI__ || tauriWindow.__TAURI_INTERNALS__)
}

async function loadGeneratedConfig(): Promise<SidecarClientConfig> {
  try {
    const mod = (await import("@/generated/sidecar-config")) as {
      sidecarClientConfig: SidecarClientConfig
    }
    return mod.sidecarClientConfig
  } catch {
    return {
      baseUrl: "http://127.0.0.1:18765",
      authHeaderValue: "Bearer dev-token",
    }
  }
}

async function resolveSidecarClientConfig(): Promise<SidecarClientConfig> {
  if (cachedConfig) {
    return cachedConfig
  }

  if (isTauriRuntime()) {
    cachedConfig = await invoke<SidecarClientConfig>("get_sidecar_client_config")
    return cachedConfig
  }

  cachedConfig = await loadGeneratedConfig()
  return cachedConfig
}

function ensureAbsolutePath(path: string): string {
  if (path.startsWith("/")) {
    return path
  }
  return `/${path}`
}

export async function sidecarRequest(path: string, init?: RequestInit): Promise<Response> {
  const config = await resolveSidecarClientConfig()
  const url = `${config.baseUrl}${ensureAbsolutePath(path)}`
  const headers = new Headers(init?.headers)
  headers.set("Authorization", config.authHeaderValue)

  return fetch(url, {
    ...init,
    headers,
    cache: "no-store",
  })
}
