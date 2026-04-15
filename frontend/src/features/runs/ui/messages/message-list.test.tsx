import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import type { ConversationEvent } from "@/shared/api/sidecar"
import { MessageList } from "./message-list"
import type { ConversationMessage } from "./types"

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

describe("MessageList ACP mapping", () => {
  it("renders ACP tool call and available commands", () => {
    const messages: ConversationMessage[] = [
      { id: "timeline:1", type: "timeline_entry", entry: timelineEntry },
      {
        id: "tool-call:1",
        type: "acp_tool_call",
        entry: {
          toolCallId: "tool-1",
          title: "write",
          kind: "write",
          status: "in_progress",
          rawInput: { command: "echo hi" },
          content: [],
        },
      },
      {
        id: "commands",
        type: "available_commands",
        commands: [{ name: "write", description: "write file", hint: "path" }],
      },
    ]

    render(
      <MessageList
        messages={messages}
        renderConversationEvent={(entry) => <div>{entry.contentText}</div>}
        renderLivePlanningEntry={(entry) => <div>{entry.content}</div>}
        onAllowPermissionRequest={vi.fn()}
        onAllowAlwaysPermissionRequest={vi.fn()}
        onRejectPermissionRequest={vi.fn()}
        onCancelPermissionRequest={vi.fn()}
      />
    )

    expect(screen.getByText("hello")).toBeInTheDocument()
    expect(screen.getAllByText("write").length).toBeGreaterThan(0)
    expect(screen.getByText(/Tool Call ID: tool-1/)).toBeInTheDocument()
    expect(screen.getByText(/Available Commands/)).toBeInTheDocument()
  })

  it("renders ACP permission card and triggers actions", () => {
    const onAllowAlwaysPermissionRequest = vi.fn()

    render(
      <MessageList
        messages={[
          {
            id: "perm",
            type: "acp_permission",
            request: {
              sessionId: "sess-1",
              toolCallId: "tool-1",
              title: "Need permission",
              kind: "write",
              options: [
                { optionId: "allow-always-id", name: "Allow always", kind: "allow_always" },
              ],
            },
          },
        ]}
        renderConversationEvent={() => null}
        renderLivePlanningEntry={() => null}
        onAllowPermissionRequest={vi.fn()}
        onAllowAlwaysPermissionRequest={onAllowAlwaysPermissionRequest}
        onRejectPermissionRequest={vi.fn()}
        onCancelPermissionRequest={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole("button", { name: "Allow always" }))
    expect(onAllowAlwaysPermissionRequest).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/Response sent/)).toBeInTheDocument()
  })
})
