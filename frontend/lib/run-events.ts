import type { TimelineEntry } from "@/lib/sidecar-client"

export type RunDeltaEvent = {
  type: "run.delta"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  stepId: string
  text: string
}

export type RunTimelineEntryEvent = {
  type: "run.timeline-entry"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  entry: TimelineEntry
}

export type RunStatusEvent = {
  type: "run.status"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  status: "queued" | "running" | "completed" | "failed" | "cancelled"
  message: string | null
}

export type RunCompletedEvent = {
  type: "run.completed"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  finalAnswer: string
}

export type RunFailedEvent = {
  type: "run.failed"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  errorCode: string
  errorMessage: string
}

export type RunKeepaliveEvent = {
  type: "run.keepalive"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
}

export type RunReplayReadyEvent = {
  type: "run.replay-ready"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  lastEventSequence: number
}

export type RunStreamEvent =
  | RunDeltaEvent
  | RunTimelineEntryEvent
  | RunStatusEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunKeepaliveEvent
  | RunReplayReadyEvent

export type RunEventEnvelope = {
  id: string
  event: RunStreamEvent["type"]
  data: RunStreamEvent
}

export function parseRunEventEnvelope(raw: string): RunEventEnvelope {
  const envelope = JSON.parse(raw) as RunEventEnvelope
  if (!envelope?.id || !envelope?.event || !envelope?.data?.type) {
    throw new Error("Invalid run event envelope")
  }
  return envelope
}
