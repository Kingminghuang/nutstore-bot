import { useEffect, useRef, useState } from "react"
import { ArrowDown } from "lucide-react"

import type { ConversationEvent } from "@/shared/api/sidecar"
import type { ConversationMessage } from "./messages/types"
import { MessageList } from "./messages/message-list"
import { cn } from "@/shared/lib"

export function ConversationStream({
  hasMessages,
  activeProjectLabel,
  hasMoreHistory,
  isLoadingHistory,
  onLoadEarlierTimeline,
  messages,
  onAllowPermissionRequest,
  onAllowAlwaysPermissionRequest,
  onRejectPermissionRequest,
  onCancelPermissionRequest,
  renderConversationEvent,
  renderLivePlanningEntry,
  showPreStepTurnLoading,
  showGenerating,
  generatingIndicator,
}: {
  hasMessages: boolean
  activeProjectLabel: string
  hasMoreHistory: boolean
  isLoadingHistory: boolean
  onLoadEarlierTimeline: () => Promise<void>
  messages: ConversationMessage[]
  onAllowPermissionRequest: () => void
  onAllowAlwaysPermissionRequest: () => void
  onRejectPermissionRequest: () => void
  onCancelPermissionRequest: () => void
  renderConversationEvent: (entry: ConversationEvent) => React.ReactNode
  renderLivePlanningEntry: (entry: { id: string; content: string; priority: "high" | "medium" | "low" | null; status: "pending" | "completed" | "failed" }) => React.ReactNode
  showPreStepTurnLoading: boolean
  showGenerating: boolean
  generatingIndicator: React.ReactNode
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const pendingHistoryScrollRestore = useRef<{ previousTop: number; previousHeight: number } | null>(null)

  useEffect(() => {
    const scrollElement = scrollRef.current
    if (!scrollElement) {
      return
    }
    const restore = pendingHistoryScrollRestore.current
    if (restore) {
      scrollElement.scrollTop = restore.previousTop + (scrollElement.scrollHeight - restore.previousHeight)
      pendingHistoryScrollRestore.current = null
      return
    }
    scrollElement.scrollTop = scrollElement.scrollHeight
  }, [messages])

  const handleLoadEarlierTimeline = async () => {
    if (!hasMoreHistory || isLoadingHistory) {
      return
    }
    const scrollElement = scrollRef.current
    if (scrollElement) {
      pendingHistoryScrollRestore.current = {
        previousTop: scrollElement.scrollTop,
        previousHeight: scrollElement.scrollHeight,
      }
    }
    try {
      await onLoadEarlierTimeline()
    } catch {
      pendingHistoryScrollRestore.current = null
    }
  }

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setShowScrollBtn(distFromBottom > 80)
  }

  const scrollToBottom = () => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto relative">
      {!hasMessages ? (
        <div className="flex flex-col items-center justify-center h-full px-6 pb-8">
          <div className="flex flex-col items-center">
            <div className="mb-6">
              <CodexLogo />
            </div>
            <h2 className="text-3xl font-medium text-foreground mb-2">Let&apos;s start</h2>
            {activeProjectLabel ? (
              <span className="text-2xl text-muted-foreground">{activeProjectLabel}</span>
            ) : (
              <span className="text-base text-muted-foreground">Select or add a project to begin</span>
            )}
          </div>
        </div>
      ) : (
        <div className="max-w-2xl mx-auto px-6 py-6 space-y-4">
          {hasMoreHistory && (
            <div className="flex justify-center">
              <button
                onClick={() => {
                  void handleLoadEarlierTimeline()
                }}
                disabled={isLoadingHistory}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-xs transition-colors",
                  isLoadingHistory
                    ? "cursor-not-allowed border-[#e8e4e0] text-muted-foreground/60"
                    : "border-[#e0d9d2] text-muted-foreground hover:bg-[#efe9e4] hover:text-foreground"
                )}
              >
                {isLoadingHistory ? "Loading..." : "Load earlier messages"}
              </button>
            </div>
          )}

          <MessageList
            messages={messages}
            renderConversationEvent={renderConversationEvent}
            renderLivePlanningEntry={renderLivePlanningEntry}
            onAllowPermissionRequest={onAllowPermissionRequest}
            onAllowAlwaysPermissionRequest={onAllowAlwaysPermissionRequest}
            onRejectPermissionRequest={onRejectPermissionRequest}
            onCancelPermissionRequest={onCancelPermissionRequest}
          />

          {showPreStepTurnLoading ? <PreStepTurnLoading /> : null}
          {showGenerating ? generatingIndicator : null}
        </div>
      )}

      {showScrollBtn && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-4 right-4 p-2 bg-background border border-[#e8e4e0] rounded-full shadow-md hover:bg-[#efe9e4] transition-colors z-10"
          aria-label="Scroll to bottom"
        >
          <ArrowDown className="w-4 h-4 text-muted-foreground" />
        </button>
      )}
    </div>
  )
}

function CodexLogo() {
  return (
    <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M32 8C18.745 8 8 18.745 8 32C8 45.255 18.745 56 32 56C45.255 56 56 45.255 56 32C56 18.745 45.255 8 32 8Z"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M20 28C20 28 24 32 32 32C40 32 44 28 44 28"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <circle cx="22" cy="24" r="2" fill="currentColor" />
      <circle cx="42" cy="24" r="2" fill="currentColor" />
      <path d="M12 18C14 14 18 12 18 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M52 18C50 14 46 12 46 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function ThinkingDots() {
  return (
    <div className="flex gap-1 items-center py-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  )
}

function PreStepTurnLoading() {
  return (
    <div className="flex gap-3 items-start" data-testid="pre-step-turn-loading">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
      <div className="bg-[#faf8f6] border border-[#e8e4e0] rounded-2xl px-4 py-3">
        <ThinkingDots />
      </div>
    </div>
  )
}
