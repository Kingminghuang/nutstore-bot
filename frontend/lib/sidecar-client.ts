import {
  type ModelOptionsResponse,
  type ProviderCatalogResponse,
  type ProviderConnectionsResponse,
  type ProviderConnectionDetail,
  type SaveProviderPayload,
} from "@/lib/provider-settings"

type DiscoveryPayload = {
  baseUrl: string
  token: string
}

class SidecarClientError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "SidecarClientError"
    this.status = status
  }
}

const DEFAULT_DISCOVERY_URL = "/api/sidecar/discovery"

let discoveryPromise: Promise<DiscoveryPayload> | null = null

export async function getProviderCatalog(): Promise<ProviderCatalogResponse> {
  return sidecarFetch<ProviderCatalogResponse>("/provider-catalog")
}

export async function getProviders(): Promise<ProviderConnectionsResponse> {
  return sidecarFetch<ProviderConnectionsResponse>("/providers")
}

export async function getModelOptions(): Promise<ModelOptionsResponse> {
  return sidecarFetch<ModelOptionsResponse>("/model-options")
}

export async function createProvider(
  payload: SaveProviderPayload
): Promise<ProviderConnectionDetail> {
  return sidecarFetch<ProviderConnectionDetail>("/providers", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export async function updateProvider(
  providerId: string,
  payload: Partial<SaveProviderPayload>
): Promise<ProviderConnectionDetail> {
  return sidecarFetch<ProviderConnectionDetail>(`/providers/${providerId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  })
}

export async function deleteProvider(providerId: string): Promise<void> {
  await sidecarFetch<void>(`/providers/${providerId}`, { method: "DELETE" })
}

export function resetSidecarDiscoveryCache(): void {
  discoveryPromise = null
}

async function sidecarFetch<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await performSidecarFetch<T>(path, init)
  } catch (error) {
    if (!shouldRetryDiscovery(error)) {
      throw error
    }

    resetSidecarDiscoveryCache()
    return performSidecarFetch<T>(path, init)
  }
}

async function performSidecarFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const discovery = await getSidecarDiscovery()
  const response = await fetch(`${discovery.baseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${discovery.token}`,
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  })

  if (!response.ok) {
    const message = await readErrorMessage(response)
    throw new SidecarClientError(message, response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

async function getSidecarDiscovery(): Promise<DiscoveryPayload> {
  if (!discoveryPromise) {
    discoveryPromise = fetch(DEFAULT_DISCOVERY_URL, { cache: "no-store" }).then(
      async (response) => {
        if (!response.ok) {
          const message = await readErrorMessage(response)
          throw new Error(message)
        }
        return (await response.json()) as DiscoveryPayload
      }
    )
  }

  return discoveryPromise
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string }
    if (payload.detail) {
      return payload.detail
    }
  } catch {
    // Ignore non-JSON errors.
  }

  return `Request failed with status ${response.status}`
}

function shouldRetryDiscovery(error: unknown): boolean {
  if (error instanceof SidecarClientError) {
    return error.status === 401 || error.status === 403
  }

  if (error instanceof TypeError) {
    return true
  }

  return false
}
