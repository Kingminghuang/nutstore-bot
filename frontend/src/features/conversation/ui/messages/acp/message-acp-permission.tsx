import { useMemo, useState } from "react"

import type { PendingPermissionRequest } from "@/features/session"

export function MessageAcpPermission({
  request,
  onAllowOnce,
  onAllowAlways,
  onReject,
  onCancelRequest,
}: {
  request: PendingPermissionRequest
  onAllowOnce: () => void
  onAllowAlways: () => void
  onReject: () => void
  onCancelRequest: () => void
}) {
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(null)
  const [hasResponded, setHasResponded] = useState(false)

  const optionMap = useMemo(() => {
    return {
      allow_once: request.options.find((option) => option.kind === "allow_once")?.optionId ?? null,
      allow_always: request.options.find((option) => option.kind === "allow_always")?.optionId ?? null,
      reject_once: request.options.find((option) => option.kind === "reject_once")?.optionId ?? null,
    }
  }, [request.options])

  const selectAndSubmit = (action: "allow_once" | "allow_always" | "reject_once") => {
    const optionId = optionMap[action]
    if (optionId) {
      setSelectedOptionId(optionId)
    }

    if (action === "allow_once") onAllowOnce()
    if (action === "allow_always") onAllowAlways()
    if (action === "reject_once") onReject()
    setHasResponded(true)
  }

  return (
    <div className="rounded-2xl border border-[#ddd4cb] bg-[#f8f4ef] px-4 py-4 space-y-4">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-foreground">Permission required</h3>
        <p className="text-xs text-foreground/62">This request is paused before a sensitive action.</p>
      </div>

      <div className="rounded-xl border border-[#ddd4cb] bg-white/70 px-4 py-3">
        <div className="text-base font-medium text-foreground/78">{request.title}</div>
        <div className="mt-2 flex flex-wrap gap-3 text-xs text-foreground/58">
          <span>Kind: {request.kind}</span>
          <span>Request: {request.toolCallId}</span>
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-xs uppercase tracking-[0.08em] text-foreground/45">Options</div>
        <div className="space-y-1">
          {request.options.map((option) => (
            <label key={option.optionId} className="flex items-center gap-2 text-sm text-foreground/80">
              <input
                type="radio"
                name={`permission-${request.toolCallId}`}
                value={option.optionId}
                checked={selectedOptionId === option.optionId}
                onChange={() => setSelectedOptionId(option.optionId)}
              />
              <span>{option.name}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#e6ddd4] pt-3">
        <button
          type="button"
          onClick={onCancelRequest}
          className="rounded-xl px-3 py-1.5 text-sm text-foreground/60 hover:bg-[#ede4da] transition-colors"
        >
          Cancel request
        </button>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => selectAndSubmit("reject_once")}
            className="rounded-xl px-3 py-1.5 text-sm text-foreground/72 hover:bg-[#ede4da] transition-colors"
          >
            Deny
          </button>
          <button
            type="button"
            onClick={() => selectAndSubmit("allow_always")}
            className="rounded-xl border border-[#ddd4cb] bg-white px-3 py-1.5 text-sm text-foreground/80 hover:bg-[#faf8f6] transition-colors"
          >
            Allow always
          </button>
          <button
            type="button"
            onClick={() => selectAndSubmit("allow_once")}
            className="rounded-xl bg-[#211c19] px-3 py-1.5 text-sm text-white hover:bg-[#3b342e] transition-colors"
          >
            Allow once
          </button>
        </div>
      </div>

      {hasResponded ? (
        <div className="rounded-xl border border-[#d6e6d6] bg-[#f1f8f1] px-3 py-2 text-xs text-[#416141]">
          Response sent{selectedOptionId ? ` (${selectedOptionId})` : ""}.
        </div>
      ) : null}
    </div>
  )
}
