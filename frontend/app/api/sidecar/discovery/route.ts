import { NextResponse } from "next/server"

import { readSidecarDiscovery } from "@/lib/sidecar-server"

export async function GET() {
  try {
    const payload = await readSidecarDiscovery()

    return NextResponse.json({
      baseUrl: payload.baseUrl,
      token: payload.token,
    })
  } catch (error) {
    return NextResponse.json(
      {
        detail:
          error instanceof Error
            ? error.message
            : "Unable to read sidecar discovery",
      },
      { status: 500 }
    )
  }
}
