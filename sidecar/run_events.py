from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


RunStatus: TypeAlias = Literal["queued", "running", "completed", "failed", "cancelled"]
RunStepKind: TypeAlias = Literal["planning", "action"]
RunMessageRole: TypeAlias = Literal["user", "assistant", "system"]
RunEventType: TypeAlias = Literal[
    "run.delta",
    "run.step",
    "run.status",
    "run.message",
    "run.completed",
    "run.failed",
    "run.keepalive",
    "run.replay-ready",
]


@dataclass(frozen=True)
class RunUsage:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int


@dataclass(frozen=True)
class RunEventEnvelope:
    id: str
    event: RunEventType
    data: dict[str, object]


def status_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    status: RunStatus,
    message: str | None = None,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.status",
        data={
            "type": "run.status",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
            "status": status,
            "message": message,
        },
    )


def delta_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    step_id: str,
    text: str,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.delta",
        data={
            "type": "run.delta",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
            "stepId": step_id,
            "text": text,
        },
    )


def step_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    step_id: str,
    step_kind: RunStepKind,
    model_output: str,
    observations: list[str],
    error: str | None,
    usage: RunUsage,
    duration_ms: int,
    has_delta: bool,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.step",
        data={
            "type": "run.step",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
            "stepId": step_id,
            "stepKind": step_kind,
            "modelOutput": model_output,
            "observations": observations,
            "error": error,
            "usage": {
                "inputTokens": usage.input_tokens,
                "outputTokens": usage.output_tokens,
                "reasoningTokens": usage.reasoning_tokens,
            },
            "durationMs": duration_ms,
            "hasDelta": has_delta,
        },
    )


def message_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    message_id: str,
    role: RunMessageRole,
    content: str,
    step_id: str | None,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.message",
        data={
            "type": "run.message",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
            "messageId": message_id,
            "role": role,
            "content": content,
            "stepId": step_id,
        },
    )


def completed_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    final_answer: str,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.completed",
        data={
            "type": "run.completed",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
            "finalAnswer": final_answer,
        },
    )


def failed_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    error_code: str,
    error_message: str,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.failed",
        data={
            "type": "run.failed",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
            "errorCode": error_code,
            "errorMessage": error_message,
        },
    )


def keepalive_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.keepalive",
        data={
            "type": "run.keepalive",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
        },
    )


def replay_ready_event(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    last_event_sequence: int,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        id=f"{run_id}:{sequence}",
        event="run.replay-ready",
        data={
            "type": "run.replay-ready",
            "runId": run_id,
            "sessionId": session_id,
            "sequence": sequence,
            "createdAt": created_at,
            "lastEventSequence": last_event_sequence,
        },
    )
