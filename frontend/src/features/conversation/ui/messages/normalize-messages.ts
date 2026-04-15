import type { LiveTurn, PendingPermissionRequest } from "@/features/session"
import type { ConversationEvent } from "@/shared/api/sidecar"
import type { ConversationMessage } from "./types"

export function normalizeConversationMessages({
  timelineEvents,
  liveTurn,
  pendingPermissionRequest,
}: {
  timelineEvents: ConversationEvent[]
  liveTurn: LiveTurn | null
  pendingPermissionRequest: PendingPermissionRequest | null
}): ConversationMessage[] {
  const messages: ConversationMessage[] = timelineEvents.map((entry) => ({
    id: `timeline:${entry.id}`,
    type: "timeline_entry",
    entry,
  }))

  if (!liveTurn) {
    if (pendingPermissionRequest) {
      messages.push({
        id: `permission:${pendingPermissionRequest.toolCallId}`,
        type: "acp_permission",
        request: pendingPermissionRequest,
      })
    }
    return messages
  }

  for (const planEntry of liveTurn.planEntries) {
    messages.push({
      id: `live-plan:${planEntry.id}`,
      type: "live_plan",
      entry: planEntry,
    })
  }

  for (const toolCall of liveTurn.toolCalls) {
    messages.push({
      id: `tool-call:${toolCall.toolCallId}`,
      type: "acp_tool_call",
      entry: toolCall,
    })
  }

  if (liveTurn.availableCommands.length > 0) {
    messages.push({
      id: "available-commands",
      type: "available_commands",
      commands: liveTurn.availableCommands,
    })
  }

  if (liveTurn.thinkingDraft.trim()) {
    messages.push({
      id: "thinking",
      type: "thinking",
      content: liveTurn.thinkingDraft,
    })
  }

  if (liveTurn.assistantDraft.trim()) {
    messages.push({
      id: "assistant-draft",
      type: "assistant_draft",
      content: liveTurn.assistantDraft,
    })
  }

  if (pendingPermissionRequest) {
    messages.push({
      id: `permission:${pendingPermissionRequest.toolCallId}`,
      type: "acp_permission",
      request: pendingPermissionRequest,
    })
  }

  return messages
}
