import type { LiveTurn, PendingPermissionRequest } from "@/features/session"
import type { ConversationEvent } from "@/shared/api/sidecar"

export type ConversationMessage =
  | { id: string; type: "timeline_entry"; entry: ConversationEvent }
  | { id: string; type: "live_plan"; entry: LiveTurn["planEntries"][number] }
  | { id: string; type: "acp_tool_call"; entry: LiveTurn["toolCalls"][number] }
  | { id: string; type: "available_commands"; commands: LiveTurn["availableCommands"] }
  | { id: string; type: "thinking"; content: string }
  | { id: string; type: "assistant_draft"; content: string }
  | { id: string; type: "acp_permission"; request: PendingPermissionRequest }
