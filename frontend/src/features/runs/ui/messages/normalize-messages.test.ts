import { describe, expect, it } from "vitest"

import type { LiveTurn } from "@/features/session"
import type { ConversationEvent } from "@/shared/api/sidecar"
import { normalizeConversationMessages } from "./normalize-messages"

const timelineEntry: ConversationEvent = {
  id: "entry-1",
  sessionId: "sess-1",
  runId: null,
  sequenceNo: 1,
  entryKind: "user_input",
  displayRole: "user",
  stepId: null,
  stepNumber: null,
  contentText: "hello",
  createdAt: "2026-01-01T00:00:00Z",
}

function buildLiveTurn(): LiveTurn {
  return {
    optimisticEvents: [],
    truncatedAfterSequence: null,
    assistantDraft: "draft answer",
    thinkingDraft: "thinking...",
    planEntries: [
      {
        id: "plan-1",
        content: "inspect files",
        priority: "medium",
        status: "pending",
      },
    ],
    toolCalls: [
      {
        toolCallId: "tool-1",
        title: "write",
        kind: "write",
        status: "in_progress",
        rawInput: { command: "echo hi" },
        content: [],
      },
    ],
    availableCommands: [
      {
        name: "write",
        description: "write file",
      },
    ],
    waitingForPermission: true,
  }
}

describe("normalizeConversationMessages", () => {
  it("preserves timeline order and appends live ACP messages", () => {
    const messages = normalizeConversationMessages({
      timelineEvents: [timelineEntry],
      liveTurn: buildLiveTurn(),
      pendingPermissionRequest: {
        sessionId: "sess-1",
        toolCallId: "tool-1",
        title: "Need permission",
        kind: "write",
        options: [],
      },
    })

    expect(messages.map((message) => message.type)).toEqual([
      "timeline_entry",
      "live_plan",
      "acp_tool_call",
      "available_commands",
      "thinking",
      "assistant_draft",
      "acp_permission",
    ])
  })

  it("keeps permission message even without live turn", () => {
    const messages = normalizeConversationMessages({
      timelineEvents: [timelineEntry],
      liveTurn: null,
      pendingPermissionRequest: {
        sessionId: "sess-1",
        toolCallId: "tool-1",
        title: "Need permission",
        kind: "write",
        options: [],
      },
    })

    expect(messages.at(-1)?.type).toBe("acp_permission")
  })
})
