import type { ConversationEvent } from "@/shared/api/sidecar"

import { MessageAcpPermission } from "./acp/message-acp-permission"
import { MessageAcpToolCall } from "./acp/message-acp-tool-call"
import { MessageAvailableCommands } from "./acp/message-available-commands"
import type { ConversationMessage } from "./types"

export function MessageList({
  messages,
  renderConversationEvent,
  renderLivePlanningEntry,
  onAllowPermissionRequest,
  onAllowAlwaysPermissionRequest,
  onRejectPermissionRequest,
  onCancelPermissionRequest,
}: {
  messages: ConversationMessage[]
  renderConversationEvent: (entry: ConversationEvent) => React.ReactNode
  renderLivePlanningEntry: (entry: { id: string; content: string; priority: "high" | "medium" | "low" | null; status: "pending" | "completed" | "failed" }) => React.ReactNode
  onAllowPermissionRequest: () => void
  onAllowAlwaysPermissionRequest: () => void
  onRejectPermissionRequest: () => void
  onCancelPermissionRequest: () => void
}) {
  return (
    <>
      {messages.map((message) => {
        switch (message.type) {
          case "timeline_entry":
            return <div key={message.id}>{renderConversationEvent(message.entry)}</div>
          case "live_plan":
            return <div key={message.id}>{renderLivePlanningEntry(message.entry)}</div>
          case "acp_tool_call":
            return <MessageAcpToolCall key={message.id} toolCall={message.entry} />
          case "available_commands":
            return <MessageAvailableCommands key={message.id} commands={message.commands} />
          case "thinking":
            return (
              <div key={message.id} className="rounded-2xl border border-[#e8e4e0] bg-[#fcfaf8] px-4 py-3">
                <div className="mb-2 text-xs uppercase tracking-[0.12em] text-foreground/45">Thinking</div>
                <p className="text-sm text-foreground/80 whitespace-pre-wrap">{message.content}</p>
              </div>
            )
          case "assistant_draft":
            return (
              <div key={message.id} data-testid="live-assistant-draft" className="flex gap-3 items-start">
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="space-y-2 text-sm text-foreground/85 whitespace-pre-wrap">{message.content}</div>
                </div>
              </div>
            )
          case "acp_permission":
            return (
              <MessageAcpPermission
                key={message.id}
                request={message.request}
                onAllowOnce={onAllowPermissionRequest}
                onAllowAlways={onAllowAlwaysPermissionRequest}
                onReject={onRejectPermissionRequest}
                onCancelRequest={onCancelPermissionRequest}
              />
            )
          default:
            return null
        }
      })}
    </>
  )
}
