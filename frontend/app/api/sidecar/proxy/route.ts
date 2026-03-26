import { NextRequest, NextResponse } from "next/server"

import { proxySidecarRequest } from "@/lib/sidecar-server"

export async function GET(request: NextRequest) {
  return handleProxyRequest(request)
}

export async function POST(request: NextRequest) {
  return handleProxyRequest(request)
}

export async function PATCH(request: NextRequest) {
  return handleProxyRequest(request)
}

export async function DELETE(request: NextRequest) {
  return handleProxyRequest(request)
}

async function handleProxyRequest(request: NextRequest) {
  const routePath = request.nextUrl.searchParams.get("path")
  if (!routePath) {
    return NextResponse.json({ detail: "Missing sidecar path" }, { status: 400 })
  }

  const headers = new Headers()
  const contentType = request.headers.get("content-type")
  if (contentType) {
    headers.set("Content-Type", contentType)
  }

  const body =
    request.method === "GET" || request.method === "DELETE"
      ? undefined
      : contentType?.toLowerCase().startsWith("application/json")
        ? await request.text()
        : await request.arrayBuffer()

  const response = await proxySidecarRequest(routePath, {
    method: request.method,
    headers,
    body,
  })

  return new NextResponse(response.body, {
    status: response.status,
    headers: response.headers,
  })
}
