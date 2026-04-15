import { invoke } from "@tauri-apps/api/core"
import { listen, type UnlistenFn } from "@tauri-apps/api/event"
import { isTauriRuntime } from "./sidecar-transport"

export type JsonRpcId = number

export type AcpServerRequest = {
  id: JsonRpcId
  method: string
  params?: Record<string, unknown>
}

export type AcpServerNotification = {
  method: string
  params?: Record<string, unknown>
}

type ServerRequestHandler = (request: AcpServerRequest) => Promise<unknown>
type NotificationHandler = (notification: AcpServerNotification) => void

class AcpClient {
  private requestHandler: ServerRequestHandler | null = null
  private notificationHandler: NotificationHandler | null = null
  private readyPromise: Promise<void> | null = null
  private unlisten: UnlistenFn | null = null
  private readonly subscriberId = "frontend-main"

  async connect(): Promise<void> {
    if (this.readyPromise) {
      return this.readyPromise
    }
    this.readyPromise = this.connectIpc()
    return this.readyPromise
  }

  private async connectIpc(): Promise<void> {
    if (!isTauriRuntime()) {
      throw new Error("ACP IPC bridge is only available in Tauri runtime")
    }
    await invoke<boolean>("acp_connect")
    await invoke<void>("acp_subscribe", { subscriberId: this.subscriberId })

    this.unlisten = await listen<{ id?: unknown; method?: unknown; params?: unknown }>(
      "acp-notification",
      (event) => {
        const payload = event.payload
        const method = typeof payload?.method === "string" ? payload.method : ""
        if (!method) {
          return
        }
        const requestId = typeof payload?.id === "number" ? payload.id : null
        if (requestId != null) {
          void this.handleServerRequest({
            id: requestId,
            method,
            params: payload.params as Record<string, unknown> | undefined,
          })
          return
        }
        this.notificationHandler?.({
          method,
          params: payload.params as Record<string, unknown> | undefined,
        })
      }
    )
  }

  private async handleServerRequest(request: AcpServerRequest): Promise<void> {
    const result = this.requestHandler
      ? await this.requestHandler(request)
      : { outcome: { outcome: "cancelled" } }
    await invoke("acp_respond", {
      requestId: request.id,
      result,
    })
  }

  onServerRequest(handler: ServerRequestHandler): void {
    this.requestHandler = handler
  }

  onNotification(handler: NotificationHandler): void {
    this.notificationHandler = handler
  }

  async request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
    await this.connect()
    const result = await invoke<unknown>("acp_request", { method, params: params ?? null })
    return result as T
  }

  async notify(method: string, params?: Record<string, unknown>): Promise<void> {
    await this.connect()
    await invoke("acp_request", { method, params: params ?? null })
  }

  async disconnect(): Promise<void> {
    if (!isTauriRuntime()) {
      return
    }
    if (this.unlisten) {
      this.unlisten()
      this.unlisten = null
    }
    await invoke<void>("acp_unsubscribe", { subscriberId: this.subscriberId })
    await invoke<boolean>("acp_disconnect")
    this.readyPromise = null
  }
}

export const acpClient = new AcpClient()
