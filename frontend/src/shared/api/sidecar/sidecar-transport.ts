import { invoke } from "@tauri-apps/api/core"

export type SidecarClientConfig = {
  baseUrl: string
  authHeaderValue: string
}

export type SidecarStreamSubscription = {
  close: () => void
}

export type SidecarStreamHandlers = {
  onEvent: (eventName: string, payload: unknown, eventId: string) => void
  onError?: (error: unknown) => void
  onClose?: () => void
}

let cachedConfig: SidecarClientConfig | null = null

function isTauriRuntime(): boolean {
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

function parseSseBlock(block: string): { id: string; event: string; data: string } | null {
  let id = ""
  let event = "message"
  const dataLines: string[] = []

  for (const line of block.split("\n")) {
    if (line.startsWith("id:")) {
      id = line.slice(3).trim()
      continue
    }
    if (line.startsWith("event:")) {
      event = line.slice(6).trim()
      continue
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (dataLines.length === 0) {
    return null
  }

  return {
    id,
    event,
    data: dataLines.join("\n"),
  }
}

export async function subscribeSidecarStream(
  path: string,
  handlers: SidecarStreamHandlers
): Promise<SidecarStreamSubscription> {
  const isTest =
    typeof process !== "undefined" &&
    typeof process.env === "object" &&
    process.env.VITEST === "true"

  if (isTest && typeof EventSource !== "undefined") {
    const config = await resolveSidecarClientConfig()
    const source = new EventSource(`${config.baseUrl}${ensureAbsolutePath(path)}`)
    const eventNames = ["run.status", "run.timeline-entry", "run.completed", "run.failed", "run.replay-ready"]
    const listener = (event: MessageEvent<string>) => {
      try {
        handlers.onEvent(event.type, JSON.parse(event.data), event.lastEventId)
      } catch (error) {
        handlers.onError?.(error)
      }
    }
    for (const eventName of eventNames) {
      source.addEventListener(eventName, listener as EventListener)
    }
    source.onerror = () => handlers.onError?.(new Error("stream error"))
    return {
      close: () => {
        source.close()
        handlers.onClose?.()
      },
    }
  }

  const controller = new AbortController()
  const response = await sidecarRequest(path, {
    method: "GET",
    signal: controller.signal,
    headers: {
      Accept: "text/event-stream",
    },
  })

  if (!response.ok || !response.body) {
    throw new Error(`Failed to open stream: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let closed = false

  const pump = async () => {
    try {
      while (!closed) {
        const { value, done } = await reader.read()
        if (done) {
          break
        }
        buffer += decoder.decode(value, { stream: true })
        while (true) {
          const boundary = buffer.indexOf("\n\n")
          if (boundary < 0) {
            break
          }
          const rawBlock = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const parsed = parseSseBlock(rawBlock)
          if (!parsed) {
            continue
          }
          try {
            handlers.onEvent(parsed.event, JSON.parse(parsed.data), parsed.id)
          } catch (error) {
            handlers.onError?.(error)
          }
        }
      }
    } catch (error) {
      if (!closed) {
        handlers.onError?.(error)
      }
    } finally {
      if (!closed) {
        closed = true
        handlers.onClose?.()
      }
    }
  }

  void pump()

  return {
    close: () => {
      if (closed) {
        return
      }
      closed = true
      controller.abort()
      handlers.onClose?.()
    },
  }
}
