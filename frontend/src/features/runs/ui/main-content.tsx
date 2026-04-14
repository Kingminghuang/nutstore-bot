"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  AlertCircle,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  Copy,
  Mic,
  Pencil,
  Paperclip,
  Plus,
  ShieldCheck,
  Square,
  X,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import type {
  ComposerAttachment,
  DraftAttachment,
  LiveTurn,
  PendingPermissionRequest,
  PermissionMode,
  Project,
  Session,
} from "@/features/session"
import type { ActionEntry, PlanningEntry, TimelineEntry } from "@/shared/api/sidecar"
import {
  getModelOptionLabel,
  getReasoningEffortOptions,
  type ModelOptionGroup,
  type SelectedReasoningEffort,
  type SelectedModelRef,
} from "@/features/providers"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/shared/ui/tooltip"
import { cn } from "@/shared/lib"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/shared/ui/dropdown-menu"

const EMPTY_ENTRIES: TimelineEntry[] = []

interface MainContentProps {
  activeProject: Project | null
  activeSession: Session | null
  timelineEntries: TimelineEntry[]
  liveTurn: LiveTurn | null
  isDraftSession: boolean
  onSendMessage: (text: string, options: { autoAllow: boolean }) => Promise<void>
  onCancelRun?: () => Promise<void> | void
  modelOptionGroups: ModelOptionGroup[]
  selectedModel: SelectedModelRef | null
  selectedReasoningEffort: SelectedReasoningEffort
  onSelectedModelChange: (value: SelectedModelRef | null) => void
  onSelectedReasoningEffortChange: (value: SelectedReasoningEffort) => void
  isLoadingModels: boolean
  providerError: string | null
  runError: string | null
  hasMoreHistory: boolean
  isLoadingHistory: boolean
  onLoadEarlierTimeline: () => Promise<void>
  composerAttachments: Array<ComposerAttachment | DraftAttachment>
  isUploadingAttachment: boolean
  onAttachFiles: (files: File[]) => Promise<void>
  onRemoveAttachment: (attachmentId: string) => Promise<void>
  onEditTimelineEntryAndRerun: (
    entryId: string,
    nextContent: string,
    options: { autoAllow: boolean }
  ) => Promise<void>
  pendingPermissionRequest: PendingPermissionRequest | null
  onAllowPermissionRequest: () => void
  onAllowAlwaysPermissionRequest: () => void
  onRejectPermissionRequest: () => void
  onCancelPermissionRequest: () => void
  onOpenSettings?: () => void
  isSessionRunning?: boolean
}

export function MainContent({
  activeProject,
  activeSession,
  timelineEntries,
  liveTurn,
  isDraftSession,
  onSendMessage,
  onCancelRun,
  modelOptionGroups,
  selectedModel,
  selectedReasoningEffort,
  onSelectedModelChange,
  onSelectedReasoningEffortChange,
  isLoadingModels,
  providerError,
  runError,
  hasMoreHistory,
  isLoadingHistory,
  onLoadEarlierTimeline,
  composerAttachments,
  isUploadingAttachment,
  onAttachFiles,
  onRemoveAttachment,
  onEditTimelineEntryAndRerun,
  pendingPermissionRequest,
  onAllowPermissionRequest,
  onAllowAlwaysPermissionRequest,
  onRejectPermissionRequest,
  onCancelPermissionRequest,
  onOpenSettings,
  isSessionRunning = false,
}: MainContentProps) {
  const [permissionOpen, setPermissionOpen] = useState(false)
  const [permission, setPermission] = useState<PermissionMode>("auto_allow")
  const [modelOpen, setModelOpen] = useState(false)
  const [reasoningOpen, setReasoningOpen] = useState(false)
  const [inputValue, setInputValue] = useState("")
  const [isGenerating, setIsGenerating] = useState(false)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [hasAnimatedProviderNotice, setHasAnimatedProviderNotice] = useState(false)
  const [editingEntryId, setEditingEntryId] = useState<string | null>(null)
  const [editingValue, setEditingValue] = useState("")
  const [isSubmittingEdit, setIsSubmittingEdit] = useState(false)
  const [copiedEntryId, setCopiedEntryId] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const copyResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingHistoryScrollRestore = useRef<{ previousTop: number; previousHeight: number } | null>(null)

  const messages = timelineEntries ?? EMPTY_ENTRIES
  const canCompose = activeSession != null || isDraftSession
  const hasMessages = messages.length > 0
  const hasAvailableModels = modelOptionGroups.some((group) => group.models.length > 0)
  const reasoningEffortOptions = useMemo(
    () => getReasoningEffortOptions(selectedModel, modelOptionGroups),
    [modelOptionGroups, selectedModel]
  )
  const supportsReasoningEffort = reasoningEffortOptions.length > 0
  const selectedReasoningEffortLabel = selectedReasoningEffort ?? "Auto"
  const showProviderNotice = Boolean(providerError || (!hasAvailableModels && !isLoadingModels))
  const lastUserMessageIndex = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].entryKind === "user_input") {
        return i
      }
    }
    return -1
  }, [messages])
  const currentTurnEntries = useMemo(
    () => (lastUserMessageIndex >= 0 ? messages.slice(lastUserMessageIndex + 1) : []),
    [lastUserMessageIndex, messages]
  )
  const latestCurrentTurnActionId = useMemo(() => {
    for (let i = currentTurnEntries.length - 1; i >= 0; i -= 1) {
      const entry = currentTurnEntries[i]
      if (entry.entryKind === "action") {
        return entry.id
      }
    }
    return null
  }, [currentTurnEntries])
  const hasCurrentTurnStepCard = useMemo(
    () =>
      currentTurnEntries.some(
        (entry) => entry.entryKind === "planning" || entry.entryKind === "action"
      ),
    [currentTurnEntries]
  )
  const hasLiveStepCard = (liveTurn?.planEntries.length ?? 0) > 0 || (liveTurn?.toolCalls.length ?? 0) > 0
  const hasLiveAssistantDraft = Boolean(liveTurn?.assistantDraft.trim())
  const showPreStepLoading =
    isSessionRunning &&
    hasMessages &&
    lastUserMessageIndex >= 0 &&
    !hasCurrentTurnStepCard &&
    !hasLiveStepCard &&
    !hasLiveAssistantDraft &&
    !isGenerating

  useEffect(() => {
    if (!showProviderNotice || hasAnimatedProviderNotice) {
      return
    }
    const frame = requestAnimationFrame(() => {
      setHasAnimatedProviderNotice(true)
    })
    return () => cancelAnimationFrame(frame)
  }, [hasAnimatedProviderNotice, showProviderNotice])

  useEffect(() => {
    return () => {
      if (copyResetTimerRef.current) {
        clearTimeout(copyResetTimerRef.current)
        copyResetTimerRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!pendingPermissionRequest) {
      return
    }
    setModelOpen(false)
    setReasoningOpen(false)
    setPermissionOpen(false)
    inputRef.current?.blur()
  }, [pendingPermissionRequest])

  const selectedModelLabel = useMemo(() => {
    if (isLoadingModels) {
      return "Loading models..."
    }
    return (
      getModelOptionLabel(selectedModel, modelOptionGroups) ??
      (providerError ? "NSBot unavailable" : "No configured providers")
    )
  }, [isLoadingModels, modelOptionGroups, providerError, selectedModel])

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

  const handleSubmit = async () => {
    const text = inputValue.trim()
    if (!text || isGenerating || !canCompose || !selectedModel) return
    setIsGenerating(true)
    setInputValue("")
    try {
      await onSendMessage(text, { autoAllow: permission === "auto_allow" })
    } catch {
      // Parent state already surfaces request errors.
    } finally {
      setIsGenerating(false)
    }
  }

  const handleFilePickerClick = () => {
    fileInputRef.current?.click()
  }

  const handleFilesSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    void onAttachFiles(files)
    e.target.value = ""
  }

  const removeFile = (attachmentId: string) => {
    void onRemoveAttachment(attachmentId)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  const copyMessage = async (entryId: string, content: string) => {
    if (typeof navigator === "undefined") {
      return
    }
    const clipboard = navigator.clipboard
    if (!clipboard || typeof clipboard.writeText !== "function") {
      return
    }
    try {
      await clipboard.writeText(content)
      setCopiedEntryId(entryId)
      if (copyResetTimerRef.current) {
        clearTimeout(copyResetTimerRef.current)
      }
      copyResetTimerRef.current = setTimeout(() => {
        setCopiedEntryId((current) => (current === entryId ? null : current))
        copyResetTimerRef.current = null
      }, 3000)
    } catch {
      // Ignore clipboard failures.
    }
  }

  const startEditTimelineEntry = (entryId: string, content: string) => {
    if (isSubmittingEdit) {
      return
    }
    setEditingEntryId(entryId)
    setEditingValue(content)
  }

  const cancelEditTimelineEntry = () => {
    if (isSubmittingEdit) {
      return
    }
    setEditingEntryId(null)
    setEditingValue("")
  }

  const submitEditedTimelineEntry = async () => {
    if (!editingEntryId || isSubmittingEdit) {
      return
    }
    const nextContent = editingValue.trim()
    if (!nextContent) {
      return
    }
    setIsSubmittingEdit(true)
    try {
      await onEditTimelineEntryAndRerun(editingEntryId, nextContent, {
        autoAllow: permission === "auto_allow",
      })
      setEditingEntryId(null)
      setEditingValue("")
    } catch {
      // Parent state already surfaces request errors.
    } finally {
      setIsSubmittingEdit(false)
    }
  }

  const permissionOptions: { value: PermissionMode; label: string }[] = [
    { value: "auto_allow", label: "Auto-allow" },
    { value: "ask", label: "Ask first" },
  ]

  const activeSessionLabel = activeSession?.title ?? "New session"
  const activeProjectLabel = activeProject?.name ?? ""
  const isPermissionOverlayVisible = pendingPermissionRequest != null

  return (
    <main
      className="flex-1 flex flex-col bg-background h-screen"
      onClick={() => {
        setModelOpen(false)
        setReasoningOpen(false)
        setPermissionOpen(false)
      }}
    >
      <header className="h-14 border-b border-border flex items-center px-6 flex-shrink-0">
        <h1 className="text-sm font-medium">{activeSessionLabel}</h1>
      </header>

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
            {messages.map((entry) => (
              <TimelineEntryView
                key={entry.id}
                entry={entry}
                showRunningIndicator={
                  isSessionRunning &&
                  entry.entryKind === "action" &&
                  entry.id === latestCurrentTurnActionId
                }
                isEditing={editingEntryId === entry.id}
                editingValue={editingValue}
                isSubmittingEdit={isSubmittingEdit}
                copied={copiedEntryId === entry.id}
                onCopyMessage={(entryId, content) => {
                  void copyMessage(entryId, content)
                }}
                onEditValueChange={setEditingValue}
                onStartEdit={startEditTimelineEntry}
                onCancelEdit={cancelEditTimelineEntry}
                onSubmitEdit={() => {
                  void submitEditedTimelineEntry()
                }}
              />
            ))}
            {liveTurn?.planEntries.map((entry) => (
              <LivePlanningStepCard key={entry.id} entry={entry} />
            ))}
            {liveTurn?.toolCalls.length ? (
              <LiveToolCallsCard
                toolCalls={liveTurn.toolCalls}
                waitingForPermission={liveTurn.waitingForPermission}
              />
            ) : null}
            {hasLiveAssistantDraft ? <LiveAssistantDraftRow content={liveTurn?.assistantDraft ?? ""} /> : null}
            {showPreStepLoading ? <PreStepRunLoading /> : null}
            {isGenerating && (
              <div className="flex gap-3 items-start">
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
                <div className="bg-[#faf8f6] border border-[#e8e4e0] rounded-2xl px-4 py-3">
                  <ThinkingDots />
                </div>
              </div>
            )}
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

      <div className="px-6 pb-4 flex-shrink-0">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFilesSelected}
          aria-hidden="true"
        />

        <div
          className="bg-[#faf8f6] border border-[#e8e4e0] rounded-2xl relative overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          <div
            aria-hidden={isPermissionOverlayVisible}
            className={cn(
              "transition-opacity duration-150",
              isPermissionOverlayVisible ? "pointer-events-none select-none opacity-25" : "opacity-100"
            )}
          >
          <div className="p-4">
            {composerAttachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-3">
                {composerAttachments.map((attachment) => (
                  <div
                    key={attachment.id}
                    className="flex items-center gap-1 bg-[#efe9e4] border border-[#e0d9d2] rounded-lg px-2 py-1 text-xs text-foreground/70 max-w-[180px]"
                  >
                    <Paperclip className="w-3 h-3 flex-shrink-0 text-muted-foreground" />
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="truncate">{attachment.fileName}</span>
                      </TooltipTrigger>
                      <TooltipContent side="top" sideOffset={6}>
                        {attachment.fileName}
                      </TooltipContent>
                    </Tooltip>
                    <button
                      onClick={() => removeFile(attachment.id)}
                      className="ml-0.5 flex-shrink-0 hover:text-foreground/90 transition-colors"
                      aria-label={`Remove ${attachment.fileName}`}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={canCompose ? "Ask for follow-up changes" : "Select or create a session first"}
              disabled={!canCompose || isPermissionOverlayVisible}
              aria-keyshortcuts="Enter"
              className="w-full bg-transparent text-sm placeholder:text-muted-foreground focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            />
          </div>

          {showProviderNotice && (
            <div className="px-4 pb-2">
              <div
                className={cn(
                  "inline-flex max-w-full flex-wrap items-center gap-1.5 rounded-xl border px-3 py-2 text-[12.5px] transition-all duration-200 ease-out",
                  hasAnimatedProviderNotice ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0",
                  providerError
                    ? "border-[#efc8bb] bg-[#fff3ef] text-[#8a4a38]"
                    : "border-[#e7ddd3] bg-[#f6f1ec] text-[#6e635a]"
                )}
              >
                <AlertCircle
                  className={cn("mt-[1px] h-3.5 w-3.5 flex-shrink-0", providerError ? "text-[#b45b44]" : "text-[#8d7768]")}
                  aria-hidden="true"
                />
                <span className="leading-5">
                  {providerError ?? "Configure a provider to enable model selection."}
                </span>
                {onOpenSettings && (
                  <button
                    onClick={onOpenSettings}
                    className={cn(
                      "text-[12.5px] font-medium underline underline-offset-2 transition-colors",
                      providerError ? "text-[#8a4a38] hover:text-[#6f3c2f]" : "text-[#7a4a2f] hover:text-[#5f3823]"
                    )}
                    type="button"
                  >
                    Open Settings
                  </button>
                )}
              </div>
            </div>
          )}

          {runError && <div className="px-4 pb-2 text-xs text-[#b45b44]">{runError}</div>}

          <div className="flex items-center justify-between px-4 pb-3">
            <div className="flex items-center gap-4">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span tabIndex={-1}>
                    <button
                      onClick={handleFilePickerClick}
                      disabled={!canCompose || isUploadingAttachment}
                      className={cn(
                        "p-1.5 rounded-lg transition-colors",
                        canCompose && !isUploadingAttachment
                          ? "hover:bg-[#efe9e4] text-muted-foreground hover:text-foreground"
                          : "text-muted-foreground/40 cursor-not-allowed"
                      )}
                      aria-label="Attach files from project folder"
                    >
                      {isUploadingAttachment ? <Square className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                    </button>
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={6}>
                  {canCompose ? `Pick files from ${activeProject?.path ?? "workspace"}` : "Select a session first"}
                </TooltipContent>
              </Tooltip>

              <div className="relative">
                <DropdownMenu
                  open={modelOpen}
                  onOpenChange={(nextOpen) => {
                    if (nextOpen && isLoadingModels) {
                      return
                    }
                    if (nextOpen && !hasAvailableModels) {
                      onOpenSettings?.()
                      return
                    }
                    setModelOpen(nextOpen)
                    if (nextOpen) {
                      setReasoningOpen(false)
                      setPermissionOpen(false)
                    }
                  }}
                >
                  <DropdownMenuTrigger asChild>
                    <button
                      disabled={isLoadingModels || (!hasAvailableModels && !onOpenSettings)}
                      className={cn(
                        "flex items-center gap-1.5 text-sm transition-colors outline-none",
                        hasAvailableModels && !isLoadingModels
                          ? "text-muted-foreground hover:text-foreground"
                          : onOpenSettings && !isLoadingModels
                            ? "text-muted-foreground hover:text-foreground"
                            : "text-muted-foreground/50 cursor-not-allowed"
                      )}
                    >
                      <span className="max-w-[260px] truncate">{selectedModelLabel}</span>
                      <ChevronDown className="w-3.5 h-3.5" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    side="top"
                    align="start"
                    sideOffset={10}
                    className="w-80 max-h-80 overflow-y-auto rounded-xl border-[#e8e4e0] bg-background p-0 shadow-md"
                  >
                    <DropdownMenuLabel className="px-4 py-2 text-xs font-normal text-muted-foreground">
                      Select model
                    </DropdownMenuLabel>
                    {modelOptionGroups.map((group, groupIndex) => (
                      <div key={group.connectionId}>
                        {groupIndex > 0 ? (
                          <DropdownMenuSeparator className="mx-0 my-0 bg-[#f0ebe6]" />
                        ) : null}
                        <div className="px-4 py-2 text-[11px] uppercase tracking-[0.12em] text-foreground/45">
                          {group.providerLabel}
                        </div>
                        {group.models.map((model) => {
                          const isSelected =
                            selectedModel?.connectionId === group.connectionId &&
                            selectedModel.modelId === model.modelId

                          return (
                            <DropdownMenuItem
                              key={`${group.connectionId}:${model.modelId}`}
                              onClick={() => {
                                onSelectedModelChange({
                                  connectionId: group.connectionId,
                                  modelId: model.modelId,
                                })
                                setModelOpen(false)
                              }}
                              className="justify-between rounded-none px-4 py-2 text-foreground/80 hover:bg-[#efe9e4] focus:bg-[#efe9e4]"
                            >
                              <span className="truncate">{model.label}</span>
                              {isSelected ? (
                                <Check className="w-4 h-4 text-foreground/70 flex-shrink-0" />
                              ) : null}
                            </DropdownMenuItem>
                          )
                        })}
                      </div>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              {supportsReasoningEffort && (
                <div className="relative">
                  <DropdownMenu
                    open={reasoningOpen}
                    onOpenChange={(nextOpen) => {
                      setReasoningOpen(nextOpen)
                      if (nextOpen) {
                        setModelOpen(false)
                        setPermissionOpen(false)
                      }
                    }}
                  >
                    <DropdownMenuTrigger asChild>
                      <button
                        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors outline-none"
                        aria-label="Reasoning effort"
                      >
                        <span>{selectedReasoningEffortLabel}</span>
                        <ChevronDown className="w-3.5 h-3.5" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      side="top"
                      align="start"
                      sideOffset={10}
                      className="w-44 rounded-xl border-[#e8e4e0] bg-background p-1 shadow-md"
                    >
                      {[null, ...reasoningEffortOptions].map((option) => {
                        const isSelected = (option ?? null) === (selectedReasoningEffort ?? null)
                        const label = option ?? "Auto"

                        return (
                          <DropdownMenuItem
                            key={option ?? "auto"}
                            onClick={() => {
                              onSelectedReasoningEffortChange(option)
                              setReasoningOpen(false)
                            }}
                            className="justify-between px-4 py-2 text-foreground/80 hover:bg-[#efe9e4] focus:bg-[#efe9e4]"
                          >
                            <span>{label}</span>
                            {isSelected ? (
                              <Check className="w-4 h-4 text-foreground/70 flex-shrink-0" />
                            ) : null}
                          </DropdownMenuItem>
                        )
                      })}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              )}

              <div className="relative">
                <DropdownMenu
                  open={permissionOpen}
                  onOpenChange={(nextOpen) => {
                    setPermissionOpen(nextOpen)
                    if (nextOpen) {
                      setModelOpen(false)
                      setReasoningOpen(false)
                    }
                  }}
                >
                  <DropdownMenuTrigger asChild>
                    <button className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors outline-none">
                      <ShieldCheck className="w-4 h-4" />
                      {permission === "auto_allow" ? "Auto-allow" : "Ask first"}
                      <ChevronDown className="w-3.5 h-3.5" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    side="top"
                    align="start"
                    sideOffset={10}
                    className="w-52 rounded-xl border-[#e8e4e0] bg-background p-1 shadow-md"
                  >
                    {permissionOptions.map((opt) => (
                      <DropdownMenuItem
                        key={opt.value}
                        onClick={() => {
                          setPermission(opt.value)
                          setPermissionOpen(false)
                        }}
                        className="gap-2 px-4 py-2.5 text-foreground/80 hover:bg-[#efe9e4] focus:bg-[#efe9e4]"
                      >
                        <ShieldCheck className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                        <span className="flex-1 text-left">{opt.label}</span>
                        {permission === opt.value ? (
                          <Check className="w-4 h-4 text-foreground/70 flex-shrink-0" />
                        ) : null}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button className="p-1.5 hover:bg-[#efe9e4] rounded-lg transition-colors">
                <Mic className="w-4 h-4 text-muted-foreground" />
              </button>

              {isGenerating ? (
                <button
                  onClick={() => {
                    setIsGenerating(false)
                    void onCancelRun?.()
                  }}
                  className="p-2 bg-foreground hover:bg-foreground/80 text-background rounded-full transition-colors"
                  aria-label="Stop"
                >
                  <Square className="w-4 h-4" />
                </button>
              ) : (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span tabIndex={-1}>
                      <button
                        onClick={() => {
                          void handleSubmit()
                        }}
                        disabled={!inputValue.trim() || !canCompose || !selectedModel}
                        className={cn(
                          "p-2 rounded-full transition-colors",
                          inputValue.trim() && canCompose && selectedModel
                            ? "bg-[#f5a76c] hover:bg-[#e99a5f] text-white"
                            : "bg-[#e8e4e0] text-muted-foreground cursor-not-allowed"
                        )}
                        aria-label="Send"
                        aria-keyshortcuts="Enter"
                      >
                        <ArrowUp className="w-4 h-4" />
                      </button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" sideOffset={6}>
                    Send (Enter)
                  </TooltipContent>
                </Tooltip>
              )}
            </div>
          </div>
          </div>

          {pendingPermissionRequest ? (
            <ComposerPermissionOverlay
              request={pendingPermissionRequest}
              onAllowOnce={onAllowPermissionRequest}
              onAllowAlways={() => {
                setPermission("auto_allow")
                onAllowAlwaysPermissionRequest()
              }}
              onReject={onRejectPermissionRequest}
              onCancelRun={onCancelPermissionRequest}
            />
          ) : null}
        </div>
      </div>
    </main>
  )
}

function ComposerPermissionOverlay({
  request,
  onAllowOnce,
  onAllowAlways,
  onReject,
  onCancelRun,
}: {
  request: PendingPermissionRequest
  onAllowOnce: () => void
  onAllowAlways: () => void
  onReject: () => void
  onCancelRun: () => void
}) {
  return (
    <div
      role="alertdialog"
      aria-labelledby="composer-permission-title"
      aria-describedby="composer-permission-description"
      className="absolute inset-0 z-20 flex flex-col justify-between rounded-2xl border border-[#ddd4cb] bg-[#f8f4ef]/96 p-4 backdrop-blur-[2px] sm:p-5"
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-full border border-[#f2c07f] bg-[#fff4e8] text-[#d88922]">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          </div>
          <div className="min-w-0 space-y-1">
            <h2 id="composer-permission-title" className="text-[1.05rem] font-semibold text-foreground">
              Permission required
            </h2>
            <p id="composer-permission-description" className="text-sm leading-6 text-foreground/62">
              This run is paused before a sensitive action. Choose how to proceed from the composer.
            </p>
          </div>
        </div>

        <div className="rounded-[1.4rem] border border-[#ddd4cb] bg-white/70 px-5 py-4 shadow-[0_1px_2px_rgba(15,23,42,0.05)]">
          <div className="text-[1.65rem] font-medium leading-tight text-foreground/72">{request.title}</div>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-foreground/58">
            <span className="inline-flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-[0.14em] text-foreground/42">Kind</span>
              <span className="font-medium text-foreground/72">{request.kind}</span>
            </span>
            <span className="inline-flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-[0.14em] text-foreground/42">Request</span>
              <span className="font-medium text-foreground/72">{request.toolCallId}</span>
            </span>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#e6ddd4] pt-4">
        <button
          type="button"
          onClick={onCancelRun}
          className="rounded-xl px-3 py-2 text-sm font-medium text-foreground/58 transition-colors hover:bg-[#ede4da] hover:text-foreground"
        >
          Cancel run
        </button>
        <div className="flex flex-wrap items-center justify-end gap-3">
        <button
          type="button"
          onClick={onReject}
          className="rounded-xl px-4 py-2 text-sm font-medium text-foreground/78 transition-colors hover:bg-[#ede4da] hover:text-foreground"
        >
          Deny
        </button>
        <button
          type="button"
          onClick={onAllowAlways}
          className="rounded-xl border border-[#ddd4cb] bg-white/78 px-4 py-2 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-white"
        >
          Allow always
        </button>
        <button
          type="button"
          onClick={onAllowOnce}
          className="rounded-xl bg-[#211c19] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#3b342e]"
        >
          Allow once
        </button>
        </div>
      </div>
    </div>
  )
}

function TimelineEntryView({
  entry,
  showRunningIndicator,
  copied,
  isEditing,
  editingValue,
  isSubmittingEdit,
  onCopyMessage,
  onEditValueChange,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
}: {
  entry: TimelineEntry
  showRunningIndicator: boolean
  copied: boolean
  isEditing: boolean
  editingValue: string
  isSubmittingEdit: boolean
  onCopyMessage: (entryId: string, content: string) => void
  onEditValueChange: (value: string) => void
  onStartEdit: (messageId: string, content: string) => void
  onCancelEdit: () => void
  onSubmitEdit: () => void
}) {
  if (entry.entryKind === "planning") {
    return <PlanningStepCard step={entry} />
  }
  if (entry.entryKind === "action") {
    return <ActionStepCard step={entry} showRunningIndicator={showRunningIndicator} />
  }
  if (entry.displayRole === "user") {
    return (
      <div className="flex justify-end">
        <div className="group max-w-[80%] flex flex-col items-end gap-2">
          {isEditing ? (
            <div className="w-full min-w-[320px] bg-[#f0ebe6] border border-[#e8e4e0] rounded-2xl px-4 py-3">
              <textarea
                value={editingValue}
                onChange={(event) => onEditValueChange(event.target.value)}
                disabled={isSubmittingEdit}
                rows={5}
                className="w-full resize-none bg-transparent text-sm text-foreground/90 focus:outline-none disabled:opacity-60"
              />
              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={onCancelEdit}
                  disabled={isSubmittingEdit}
                  className="rounded-2xl border border-[#e8e4e0] bg-background px-4 py-1.5 text-sm hover:bg-[#efe9e4] transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={onSubmitEdit}
                  disabled={isSubmittingEdit || editingValue.trim().length === 0}
                  className="rounded-2xl bg-foreground px-4 py-1.5 text-sm text-background hover:bg-foreground/85 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isSubmittingEdit ? "Sending..." : "Send"}
                </button>
              </div>
            </div>
          ) : (
            <div className="w-full bg-[#f0ebe6] border border-[#e8e4e0] rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm text-foreground/90 whitespace-pre-wrap">
              {entry.contentText}
            </div>
          )}

          {!isEditing && (
            <div className="flex items-center gap-2 self-end opacity-0 pointer-events-none transition-opacity group-hover:opacity-100 group-hover:pointer-events-auto group-focus-within:opacity-100 group-focus-within:pointer-events-auto">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => onCopyMessage(entry.id, entry.contentText ?? "")}
                    className="h-9 w-9 rounded-full border border-[#e8e4e0] bg-[#f3efeb] text-muted-foreground hover:text-foreground hover:bg-[#efe9e4] transition-colors inline-flex items-center justify-center"
                    aria-label="Copy user message"
                  >
                    {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={6}>
                  Copy
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => onStartEdit(entry.id, entry.contentText ?? "")}
                    className="h-9 w-9 rounded-full border border-[#e8e4e0] bg-[#f3efeb] text-muted-foreground hover:text-foreground hover:bg-[#efe9e4] transition-colors inline-flex items-center justify-center"
                    aria-label="Edit user message"
                  >
                    <Pencil className="w-4 h-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={6}>
                  Edit
                </TooltipContent>
              </Tooltip>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-3 items-start">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
      <div className="group flex-1 min-w-0 flex flex-col gap-2">
        {entry.entryKind === "final_answer" ? (
          <FinalAnswerContent content={entry.contentText ?? ""} />
        ) : (
          <AgentMessageContent content={entry.contentText ?? ""} />
        )}
        <div className="flex items-center gap-2 opacity-0 pointer-events-none transition-opacity group-hover:opacity-100 group-hover:pointer-events-auto group-focus-within:opacity-100 group-focus-within:pointer-events-auto">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => onCopyMessage(entry.id, entry.contentText ?? "")}
                className="h-9 w-9 rounded-full border border-[#e8e4e0] bg-[#f3efeb] text-muted-foreground hover:text-foreground hover:bg-[#efe9e4] transition-colors inline-flex items-center justify-center"
                aria-label="Copy assistant message"
              >
                {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" sideOffset={6}>
              Copy
            </TooltipContent>
          </Tooltip>
        </div>
      </div>
    </div>
  )
}

function PlanningStepCard({ step }: { step: PlanningEntry }) {
  return (
    <div className="rounded-2xl border border-[#e8e4e0] bg-[#fcfaf8] px-4 py-3">
      <div className="mb-2 text-xs uppercase tracking-[0.12em] text-foreground/45">Planning step</div>
      <AgentMessageContent content={step.contentText ?? ""} />
    </div>
  )
}

function LivePlanningStepCard({ entry }: { entry: LiveTurn["planEntries"][number] }) {
  return (
    <div className="rounded-2xl border border-[#e8e4e0] bg-[#fcfaf8] px-4 py-3">
      <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.12em] text-foreground/45">
        <span>Planning step</span>
        <span>{entry.status}</span>
      </div>
      <AgentMessageContent content={entry.content} />
    </div>
  )
}

function ActionStepCard({
  step,
  showRunningIndicator,
}: {
  step: ActionEntry
  showRunningIndicator: boolean
}) {
  const payload = step.contentJson
  const [showThoughtPanel, setShowThoughtPanel] = useState(false)
  const [showToolCallsPanel, setShowToolCallsPanel] = useState(false)
  const [showObservationsPanel, setShowObservationsPanel] = useState(false)
  const [showCodeActionPanel, setShowCodeActionPanel] = useState(false)
  const [showErrorPanel, setShowErrorPanel] = useState(true)
  const thoughtText = normalizeThought(payload?.thought)
  const visibleToolCalls = (payload?.toolCalls ?? []).filter(
    (toolCall) => toolCall.name !== "python_interpreter"
  )
  const hasToolCalls = visibleToolCalls.length > 0
  const hasCodeAction = Boolean(payload?.codeAction)
  const hasObservations = (payload?.observations?.length ?? 0) > 0
  const hasError = Boolean(payload?.error)

  return (
    <div className="rounded-2xl border border-[#e8e4e0] bg-[#fcfaf8] px-4 py-3 space-y-3">
      <div className="text-xs uppercase tracking-[0.12em] text-foreground/45">Step {step.stepNumber}</div>
      {thoughtText ? (
        <CollapsiblePanel
          label="Thought"
          open={showThoughtPanel}
          onToggle={() => setShowThoughtPanel((current) => !current)}
        >
          <div className="rounded-xl border border-[#e8e4e0] bg-background px-3 py-2">
            <p className="text-sm text-foreground/85 whitespace-pre-wrap">{thoughtText}</p>
          </div>
        </CollapsiblePanel>
      ) : null}
      {hasToolCalls ? (
        <CollapsiblePanel
          label="Tool calls"
          open={showToolCallsPanel}
          onToggle={() => setShowToolCallsPanel((current) => !current)}
        >
          <div className="rounded-xl border border-[#e8e4e0] bg-background px-3 py-2 text-sm text-foreground/80 space-y-2">
            {visibleToolCalls.map((toolCall, index) => (
              <div key={`${toolCall.name}-${index}`} className="whitespace-pre-wrap">
                {toolCall.name}({toolCall.argumentsText})
              </div>
            ))}
          </div>
        </CollapsiblePanel>
      ) : null}
      {hasCodeAction ? (
        <CollapsiblePanel
          label="Code action"
          open={showCodeActionPanel}
          onToggle={() => setShowCodeActionPanel((current) => !current)}
        >
          <CodeBlock label="python" content={payload?.codeAction ?? ""} />
        </CollapsiblePanel>
      ) : null}
      {hasError ? (
        <CollapsiblePanel
          label="Error"
          open={showErrorPanel}
          onToggle={() => setShowErrorPanel((current) => !current)}
        >
          <div className="rounded-xl border border-[#efc1b4] bg-[#fff1ec] px-3 py-2 text-sm text-[#9d4d38] whitespace-pre-wrap">
            {payload?.error}
          </div>
        </CollapsiblePanel>
      ) : null}
      {hasObservations ? (
        <CollapsiblePanel
          label="Observations"
          open={showObservationsPanel}
          onToggle={() => setShowObservationsPanel((current) => !current)}
        >
          <CodeBlock label="observations" content={payload?.observations.join("\n") ?? ""} />
        </CollapsiblePanel>
      ) : null}
      <StepFootnote
        usage={payload?.usage ?? { inputTokens: 0, outputTokens: 0, reasoningTokens: 0 }}
        durationMs={payload?.durationMs ?? 0}
      />
      {showRunningIndicator ? <StepRunningIndicator /> : null}
    </div>
  )
}

function CollapsiblePanel({
  label,
  open,
  onToggle,
  children,
}: {
  label: string
  open: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onToggle}
        className="text-xs uppercase tracking-[0.12em] text-foreground/45 hover:text-foreground/70 transition-colors"
      >
        {open ? `Hide ${label}` : `Show ${label}`}
      </button>
      {open ? children : null}
    </div>
  )
}

function normalizeThought(thought: string | null | undefined): string | null {
  if (!thought) {
    return null
  }
  const trimmed = thought.trim().replace(/^thought\s*:\s*/i, "")
  return trimmed.length > 0 ? trimmed : null
}

function CodeBlock({ label, content }: { label: string; content: string }) {
  return (
    <pre className="bg-[#1e1e1e] text-[#d4d4d4] rounded-xl px-4 py-3 text-xs overflow-x-auto font-mono">
      <div className="text-[#6a9955] mb-2 text-xs">{label}</div>
      <code>{content}</code>
    </pre>
  )
}

function StepFootnote({
  usage,
  durationMs,
}: {
  usage: { inputTokens: number; outputTokens: number; reasoningTokens: number }
  durationMs: number
}) {
  const parts = [`Input ${usage.inputTokens}`, `Output ${usage.outputTokens}`]
  if (usage.reasoningTokens > 0) {
    parts.push(`Reasoning ${usage.reasoningTokens}`)
  }
  if (durationMs > 0) {
    parts.push(`Duration ${(durationMs / 1000).toFixed(2)}s`)
  }
  return <div className="text-xs text-foreground/45">{parts.join(" | ")}</div>
}

function AgentMessageContent({ content }: { content: string }) {
  const parts = content.split(/(```[\s\S]*?```)/g)
  return (
    <div className="space-y-2 text-sm text-foreground/85">
      {parts.map((part, i) => {
        if (part.startsWith("```") && part.endsWith("```")) {
          const lines = part.slice(3, -3).split("\n")
          const lang = lines[0]
          const code = lines.slice(1).join("\n")
          return (
            <pre key={i} className="bg-[#1e1e1e] text-[#d4d4d4] rounded-xl px-4 py-3 text-xs overflow-x-auto font-mono">
              {lang && <div className="text-[#6a9955] mb-2 text-xs">{lang}</div>}
              <code>{code}</code>
            </pre>
          )
        }
        return (
          <p key={i} className="whitespace-pre-wrap leading-relaxed">
            {part}
          </p>
        )
      })}
    </div>
  )
}

function FinalAnswerContent({ content }: { content: string }) {
  const parts = content.split(/(```[\s\S]*?```)/g)
  return (
    <div className="space-y-2 text-sm text-foreground/85">
      {parts.map((part, i) => {
        if (part.startsWith("```") && part.endsWith("```")) {
          const lines = part.slice(3, -3).split("\n")
          const lang = lines[0]
          const code = lines.slice(1).join("\n")
          return (
            <pre key={i} className="bg-[#1e1e1e] text-[#d4d4d4] rounded-xl px-4 py-3 text-xs overflow-x-auto font-mono">
              {lang && <div className="text-[#6a9955] mb-2 text-xs">{lang}</div>}
              <code>{code}</code>
            </pre>
          )
        }

        return (
          <ReactMarkdown
            key={i}
            remarkPlugins={[remarkGfm]}
            components={{
              p: ({ children }) => <p className="whitespace-pre-wrap leading-relaxed">{children}</p>,
              ul: ({ children }) => <ul className="list-disc pl-6 space-y-1">{children}</ul>,
              ol: ({ children }) => <ol className="list-decimal pl-6 space-y-1">{children}</ol>,
              li: ({ children }) => <li className="leading-relaxed">{children}</li>,
              h1: ({ children }) => <h1 className="text-xl font-semibold leading-tight">{children}</h1>,
              h2: ({ children }) => <h2 className="text-lg font-semibold leading-tight">{children}</h2>,
              h3: ({ children }) => <h3 className="text-base font-semibold leading-tight">{children}</h3>,
              a: ({ children, href }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  {children}
                </a>
              ),
              code: ({ children }) => (
                <code className="rounded bg-[#efe9e4] px-1 py-0.5 text-[0.85em] font-mono">{children}</code>
              ),
              table: ({ children }) => (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-sm">{children}</table>
                </div>
              ),
              thead: ({ children }) => <thead className="border-b border-[#e8e4e0]">{children}</thead>,
              tbody: ({ children }) => <tbody>{children}</tbody>,
              th: ({ children }) => <th className="px-2 py-1 text-left font-semibold">{children}</th>,
              td: ({ children }) => <td className="px-2 py-1 align-top">{children}</td>,
            }}
          >
            {part}
          </ReactMarkdown>
        )
      })}
    </div>
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

function StepRunningIndicator() {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-[#e8e4e0] bg-background px-3 py-2">
      <ThinkingDots />
      <span className="text-xs text-foreground/60">Running...</span>
    </div>
  )
}

function PreStepRunLoading() {
  return (
    <div className="flex gap-3 items-start" data-testid="pre-step-run-loading">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
      <div className="bg-[#faf8f6] border border-[#e8e4e0] rounded-2xl px-4 py-3">
        <ThinkingDots />
      </div>
    </div>
  )
}

function LiveToolCallsCard({
  toolCalls,
  waitingForPermission,
}: {
  toolCalls: LiveTurn["toolCalls"]
  waitingForPermission: boolean
}) {
  return (
    <div className="rounded-2xl border border-[#e8e4e0] bg-[#fcfaf8] px-4 py-3 space-y-3">
      <div className="flex items-center justify-between text-xs uppercase tracking-[0.12em] text-foreground/45">
        <span>Tool calls</span>
        {waitingForPermission ? <span>Waiting for permission</span> : null}
      </div>
      <div className="space-y-2">
        {toolCalls.map((toolCall) => (
          <div
            key={toolCall.toolCallId}
            className="flex items-center justify-between rounded-xl border border-[#e8e4e0] bg-background px-3 py-2 text-sm"
          >
            <div className="min-w-0">
              <div className="font-medium text-foreground/85">{toolCall.title}</div>
              <div className="text-xs text-foreground/50">{toolCall.kind}</div>
            </div>
            <div className="text-xs uppercase tracking-[0.08em] text-foreground/55">{toolCall.status}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function LiveAssistantDraftRow({ content }: { content: string }) {
  return (
    <div className="flex gap-3 items-start" data-testid="live-assistant-draft">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <AgentMessageContent content={content} />
      </div>
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
