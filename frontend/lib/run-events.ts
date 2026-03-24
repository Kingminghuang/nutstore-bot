export type RunUsage = {
  inputTokens: number
  outputTokens: number
  reasoningTokens: number
}

export type RunStepKind = "planning" | "action"

export type RunDeltaEvent = {
  type: "run.delta"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  stepId: string
  text: string
}

export type RunStepEvent = {
  type: "run.step"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  stepId: string
  stepKind: RunStepKind
  modelOutput: string
  observations: string[]
  error: string | null
  usage: RunUsage
  durationMs: number
  hasDelta: boolean
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

export type RunMessageEvent = {
  type: "run.message"
  runId: string
  sessionId: string
  sequence: number
  createdAt: string
  messageId: string
  role: "user" | "assistant" | "system"
  content: string
  stepId: string | null
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
  | RunStepEvent
  | RunStatusEvent
  | RunMessageEvent
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
