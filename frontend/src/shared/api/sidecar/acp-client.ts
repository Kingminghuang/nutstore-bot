import { sidecarRequest } from "./sidecar-transport"

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

type PendingCall = {
  resolve: (value: unknown) => void
  reject: (reason?: unknown) => void
}

type ServerRequestHandler = (request: AcpServerRequest) => Promise<unknown>
type NotificationHandler = (notification: AcpServerNotification) => void

class AcpClient {
  private socket: WebSocket | null = null
  private nextId = 1
  private pending = new Map<JsonRpcId, PendingCall>()
  private requestHandler: ServerRequestHandler | null = null
  private notificationHandler: NotificationHandler | null = null
  private readyPromise: Promise<void> | null = null

  async connect(): Promise<void> {
    if (this.readyPromise) {
      return this.readyPromise
    }
    this.readyPromise = this.openSocket()
    return this.readyPromise
  }

  private async openSocket(): Promise<void> {
    const response = await sidecarRequest("/health", { method: "GET" })
    if (!response.ok) {
      throw new Error("sidecar unavailable")
    }

    const base = new URL(response.url)
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:"
    base.pathname = "/acp/ws"
    base.search = ""

    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(base.toString())
      this.socket = ws
      ws.onopen = () => resolve()
      ws.onerror = () => reject(new Error("failed to connect acp websocket"))
      ws.onmessage = (event) => {
        this.handleIncoming(String(event.data))
      }
      ws.onclose = () => {
        for (const [, call] of this.pending) {
          call.reject(new Error("acp websocket closed"))
        }
        this.pending.clear()
        this.socket = null
        this.readyPromise = null
      }
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
    const id = this.nextId++
    const payload: Record<string, unknown> = {
      jsonrpc: "2.0",
      id,
      method,
    }
    if (params) {
      payload.params = params
    }

    const response = await new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.socket?.send(JSON.stringify(payload))
    })

    return response as T
  }

  async notify(method: string, params?: Record<string, unknown>): Promise<void> {
    await this.connect()
    const payload: Record<string, unknown> = {
      jsonrpc: "2.0",
      method,
    }
    if (params) {
      payload.params = params
    }
    this.socket?.send(JSON.stringify(payload))
  }

  private handleIncoming(raw: string): void {
    let payload: any
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }

    if (payload && payload.id != null && payload.method == null) {
      const pending = this.pending.get(Number(payload.id))
      if (!pending) {
        return
      }
      this.pending.delete(Number(payload.id))
      if (payload.error) {
        pending.reject(new Error(String(payload.error.message || "rpc error")))
      } else {
        pending.resolve(payload.result)
      }
      return
    }

    if (payload && payload.method && payload.id != null) {
      if (!this.requestHandler) {
        this.socket?.send(
          JSON.stringify({
            jsonrpc: "2.0",
            id: payload.id,
            result: { outcome: { outcome: "cancelled" } },
          })
        )
        return
      }
      void this.requestHandler({ id: Number(payload.id), method: String(payload.method), params: payload.params })
        .then((result) => {
          this.socket?.send(
            JSON.stringify({
              jsonrpc: "2.0",
              id: payload.id,
              result,
            })
          )
        })
        .catch((error) => {
          this.socket?.send(
            JSON.stringify({
              jsonrpc: "2.0",
              id: payload.id,
              error: {
                code: -32000,
                message: error instanceof Error ? error.message : "request handling failed",
              },
            })
          )
        })
      return
    }

    if (payload && payload.method && payload.id == null) {
      this.notificationHandler?.({ method: String(payload.method), params: payload.params })
    }
  }
}

export const acpClient = new AcpClient()
