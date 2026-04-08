import { describe, expect, it } from "vitest"

import { parseRunEventEnvelope } from "@/features/runs"

describe("run event protocol", () => {
  it("parses a valid run event envelope", () => {
    const envelope = parseRunEventEnvelope(
      JSON.stringify({
        id: "run_1:3",
        event: "run.delta",
        data: {
          type: "run.delta",
          runId: "run_1",
          sessionId: "sess_1",
          sequence: 3,
          createdAt: "2026-03-24T12:00:00Z",
          stepId: "step-1",
          text: "hello",
        },
      })
    )

    expect(envelope.event).toBe("run.delta")
    expect(envelope.data.type).toBe("run.delta")
  })

  it("rejects invalid run event envelopes", () => {
    expect(() => parseRunEventEnvelope(JSON.stringify({ event: "run.delta" }))).toThrow(
      "Invalid run event envelope"
    )
  })
})
