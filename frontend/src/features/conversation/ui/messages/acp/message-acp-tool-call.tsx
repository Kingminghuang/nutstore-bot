import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import type { LiveToolCall } from "@/features/session"
import { cn } from "@/shared/lib"

function StatusTag({ status }: { status: LiveToolCall["status"] }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] uppercase tracking-[0.08em]",
        status === "pending" && "bg-[#eaf2ff] text-[#2f5ea5]",
        status === "in_progress" && "bg-[#fff2e3] text-[#a15d1f]",
        status === "completed" && "bg-[#ece8e3] text-foreground/65",
        status === "failed" && "bg-[#fff0ec] text-[#9d4d38]",
        status === "cancelled" && "bg-[#f1ece6] text-foreground/55"
      )}
    >
      {status.replace("_", " ")}
    </span>
  )
}

function ContentBlock({ content }: { content: NonNullable<LiveToolCall["content"]>[number] }) {
  if (content.type === "diff") {
    return (
      <div className="rounded-xl border border-[#e8e4e0] bg-background px-3 py-2 text-xs text-foreground/80 space-y-1">
        <div className="text-foreground/60">Path: {content.path ?? "(unknown)"}</div>
        {content.oldText != null ? (
          <pre className="whitespace-pre-wrap rounded bg-[#f7f3ef] px-2 py-1 text-[11px]">- {content.oldText}</pre>
        ) : null}
        {content.newText ? (
          <pre className="whitespace-pre-wrap rounded bg-[#eef8ee] px-2 py-1 text-[11px]">+ {content.newText}</pre>
        ) : null}
      </div>
    )
  }

  if (content.content?.type === "text" && content.content.text) {
    return (
      <div className="rounded-xl border border-[#e8e4e0] bg-background px-3 py-2 text-sm text-foreground/80">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.content.text}</ReactMarkdown>
      </div>
    )
  }

  return null
}

export function MessageAcpToolCall({ toolCall }: { toolCall: LiveToolCall }) {
  return (
    <div className="rounded-2xl border border-[#e8e4e0] bg-[#fcfaf8] px-4 py-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium text-foreground/85 truncate">{toolCall.title}</div>
          <div className="text-xs text-foreground/50">{toolCall.kind}</div>
        </div>
        <StatusTag status={toolCall.status} />
      </div>
      {toolCall.status === "pending" ? (
        <div className="text-xs text-foreground/55">Waiting for permission</div>
      ) : null}

      {toolCall.rawInput !== undefined ? (
        <div className="rounded-xl border border-[#e8e4e0] bg-background px-3 py-2 text-xs text-foreground/70 overflow-x-auto">
          {typeof toolCall.rawInput === "string" ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{`\`\`\`\n${toolCall.rawInput}\n\`\`\``}</ReactMarkdown>
          ) : (
            <pre>{JSON.stringify(toolCall.rawInput, null, 2)}</pre>
          )}
        </div>
      ) : null}

      {toolCall.content && toolCall.content.length > 0 ? (
        <div className="space-y-2">
          {toolCall.content.map((item, index) => (
            <ContentBlock key={`${toolCall.toolCallId}-content-${index}`} content={item} />
          ))}
        </div>
      ) : null}

      <div className="text-[11px] text-foreground/45">Tool Call ID: {toolCall.toolCallId}</div>
    </div>
  )
}
