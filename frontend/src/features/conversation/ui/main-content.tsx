"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  AlertCircle,
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
import type {
  ActionEntry,
  PlanningEntry,
  ConversationEvent,
  WorkspaceEntrySearchResult,
} from "@/shared/api/sidecar"
import {
  buildComposerBlocksFromPromptBlocks,
  INLINE_MENTION_SENTINEL,
  type ComposerInlineBlock,
} from "@/app/prompt-blocks"
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
import { ConversationStream } from "./conversation-stream"
import { normalizeConversationMessages } from "./messages/normalize-messages"

const EMPTY_ENTRIES: ConversationEvent[] = []

interface MainContentProps {
  activeProject: Project | null
  activeSession: Session | null
  timelineEvents: ConversationEvent[]
  liveTurn: LiveTurn | null
  isDraftSession: boolean
  onSendMessage: (payload: { blocks: ComposerInlineBlock[] }, options: { autoAllow: boolean }) => Promise<void>
  onCancelTurn?: () => Promise<void> | void
  onSearchWorkspaceEntries: (query: string) => Promise<WorkspaceEntrySearchResult[]>
  modelOptionGroups: ModelOptionGroup[]
  selectedModel: SelectedModelRef | null
  selectedReasoningEffort: SelectedReasoningEffort
  onSelectedModelChange: (value: SelectedModelRef | null) => void
  onSelectedReasoningEffortChange: (value: SelectedReasoningEffort) => void
  isLoadingModels: boolean
  providerError: string | null
  turnError: string | null
  hasMoreHistory: boolean
  isLoadingHistory: boolean
  onLoadEarlierTimeline: () => Promise<void>
  composerAttachments: Array<ComposerAttachment | DraftAttachment>
  isUploadingAttachment: boolean
  onAttachFiles: (files: File[]) => Promise<void>
  onRemoveAttachment: (attachmentId: string) => Promise<void>
  onEditConversationEventAndRerun: (
    entryId: string,
    nextBlocks: ComposerInlineBlock[],
    options: { autoAllow: boolean }
  ) => Promise<void>
  pendingPermissionRequest: PendingPermissionRequest | null
  onAllowPermissionRequest: () => void
  onAllowAlwaysPermissionRequest: () => void
  onRejectPermissionRequest: () => void
  onRejectAlwaysPermissionRequest: () => void
  onCancelPermissionRequest: () => void
  onOpenSettings?: () => void
  isTurnPending?: boolean
}

export function MainContent({
  activeProject,
  activeSession,
  timelineEvents,
  liveTurn,
  isDraftSession,
  onSendMessage,
  onCancelTurn,
  onSearchWorkspaceEntries,
  modelOptionGroups,
  selectedModel,
  selectedReasoningEffort,
  onSelectedModelChange,
  onSelectedReasoningEffortChange,
  isLoadingModels,
  providerError,
  turnError,
  hasMoreHistory,
  isLoadingHistory,
  onLoadEarlierTimeline,
  composerAttachments,
  isUploadingAttachment,
  onAttachFiles,
  onRemoveAttachment,
  onEditConversationEventAndRerun,
  pendingPermissionRequest,
  onAllowPermissionRequest,
  onAllowAlwaysPermissionRequest,
  onRejectPermissionRequest,
  onRejectAlwaysPermissionRequest,
  onCancelPermissionRequest,
  onOpenSettings,
  isTurnPending = false,
}: MainContentProps) {
  const [permissionOpen, setPermissionOpen] = useState(false)
  const [permission, setPermission] = useState<PermissionMode>("auto_allow")
  const [modelOpen, setModelOpen] = useState(false)
  const [reasoningOpen, setReasoningOpen] = useState(false)
  const [composerBlocks, setComposerBlocks] = useState<ComposerInlineBlock[]>([{ type: "text", text: "" }])
  const [isGenerating, setIsGenerating] = useState(false)
  const [hasAnimatedProviderNotice, setHasAnimatedProviderNotice] = useState(false)
  const [editingEntryId, setEditingEntryId] = useState<string | null>(null)
  const [editingBlocks, setEditingBlocks] = useState<ComposerInlineBlock[]>([{ type: "text", text: "" }])
  const [editingSelectionRange, setEditingSelectionRange] = useState({ start: 0, end: 0 })
  const [editingMentionResults, setEditingMentionResults] = useState<WorkspaceEntrySearchResult[]>([])
  const [editingMentionActiveIndex, setEditingMentionActiveIndex] = useState(0)
  const [isSearchingEditMentions, setIsSearchingEditMentions] = useState(false)
  const [editingMentionSearchError, setEditingMentionSearchError] = useState<string | null>(null)
  const [dismissedEditMentionKey, setDismissedEditMentionKey] = useState<string | null>(null)
  const [isSubmittingEdit, setIsSubmittingEdit] = useState(false)
  const [copiedEntryId, setCopiedEntryId] = useState<string | null>(null)
  const [mentionResults, setMentionResults] = useState<WorkspaceEntrySearchResult[]>([])
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0)
  const [isSearchingMentions, setIsSearchingMentions] = useState(false)
  const [mentionSearchError, setMentionSearchError] = useState<string | null>(null)
  const [selectionRange, setSelectionRange] = useState({ start: 0, end: 0 })
  const [dismissedMentionKey, setDismissedMentionKey] = useState<string | null>(null)

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const copyResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const editingInputRef = useRef<HTMLTextAreaElement>(null)
  const inputValue = useMemo(() => serializeComposerBlocks(composerBlocks), [composerBlocks])
  const selectedMentions = useMemo(
    () => composerBlocks.flatMap((block) => (block.type === "mention" ? [block.mention] : [])),
    [composerBlocks]
  )
  const editingValue = useMemo(() => serializeComposerBlocks(editingBlocks), [editingBlocks])
  const editingSelectedMentions = useMemo(
    () => editingBlocks.flatMap((block) => (block.type === "mention" ? [block.mention] : [])),
    [editingBlocks]
  )

  const messages = timelineEvents ?? EMPTY_ENTRIES
  const latestEditableUserMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].entryKind === "user_input") {
        return messages[i].id
      }
    }
    return null
  }, [messages])
  const canCompose = activeSession != null || isDraftSession
  const canSubmitPrompt =
    stripInlineTokenText(inputValue).trim().length > 0 || selectedMentions.length > 0 || composerAttachments.length > 0
  const hasMessages =
    messages.length > 0 ||
    (liveTurn?.planEntries.length ?? 0) > 0 ||
    (liveTurn?.toolCalls.length ?? 0) > 0 ||
    Boolean(liveTurn?.assistantDraft.trim()) ||
    Boolean(liveTurn?.thinkingDraft.trim()) ||
    (liveTurn?.availableCommands.length ?? 0) > 0 ||
    pendingPermissionRequest != null
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
  const showPreStepTurnLoading =
    isTurnPending &&
    hasMessages &&
    lastUserMessageIndex >= 0 &&
    !hasCurrentTurnStepCard &&
    !hasLiveStepCard &&
    !hasLiveAssistantDraft &&
    !isGenerating
  const activeMentionMatch = useMemo(
    () =>
      selectionRange.start === selectionRange.end
        ? getActiveMentionMatch(inputValue, selectionRange.start)
        : null,
    [inputValue, selectionRange.end, selectionRange.start]
  )
  const activeMentionKey = activeMentionMatch
    ? `${activeMentionMatch.start}:${activeMentionMatch.end}:${activeMentionMatch.query}`
    : null
  const mentionPopoverOpen =
    Boolean(canCompose && activeMentionMatch) && activeMentionKey !== dismissedMentionKey
  const activeEditMentionMatch = useMemo(
    () =>
      editingSelectionRange.start === editingSelectionRange.end
        ? getActiveMentionMatch(editingValue, editingSelectionRange.start)
        : null,
    [editingSelectionRange.end, editingSelectionRange.start, editingValue]
  )
  const activeEditMentionKey = activeEditMentionMatch
    ? `${activeEditMentionMatch.start}:${activeEditMentionMatch.end}:${activeEditMentionMatch.query}`
    : null
  const editMentionPopoverOpen =
    Boolean(editingEntryId && activeEditMentionMatch) &&
    activeEditMentionKey !== dismissedEditMentionKey

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

  useEffect(() => {
    setComposerBlocks([{ type: "text", text: "" }])
    setMentionResults([])
    setMentionSearchError(null)
    setIsSearchingMentions(false)
    setMentionActiveIndex(0)
    setDismissedMentionKey(null)
  }, [activeProject?.id])

  useEffect(() => {
    if (!activeMentionKey) {
      if (dismissedMentionKey !== null) {
        setDismissedMentionKey(null)
      }
      return
    }
    if (dismissedMentionKey && dismissedMentionKey !== activeMentionKey) {
      setDismissedMentionKey(null)
    }
  }, [activeMentionKey, dismissedMentionKey])

  useEffect(() => {
    if (!activeEditMentionKey) {
      if (dismissedEditMentionKey !== null) {
        setDismissedEditMentionKey(null)
      }
      return
    }
    if (dismissedEditMentionKey && dismissedEditMentionKey !== activeEditMentionKey) {
      setDismissedEditMentionKey(null)
    }
  }, [activeEditMentionKey, dismissedEditMentionKey])

  useEffect(() => {
    if (!mentionPopoverOpen || !activeMentionMatch) {
      setMentionResults([])
      setMentionSearchError(null)
      setIsSearchingMentions(false)
      setMentionActiveIndex(0)
      return
    }

    let cancelled = false
    setIsSearchingMentions(true)
    setMentionSearchError(null)
    const timer = setTimeout(async () => {
      try {
        const results = await onSearchWorkspaceEntries(activeMentionMatch.query)
        if (cancelled) {
          return
        }
        setMentionResults(results)
        setMentionActiveIndex(0)
      } catch (error) {
        if (cancelled) {
          return
        }
        setMentionResults([])
        setMentionSearchError(error instanceof Error ? error.message : "Failed to search workspace")
      } finally {
        if (!cancelled) {
          setIsSearchingMentions(false)
        }
      }
    }, 120)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeMentionMatch, mentionPopoverOpen, onSearchWorkspaceEntries])

  useEffect(() => {
    if (!editMentionPopoverOpen || !activeEditMentionMatch) {
      setEditingMentionResults([])
      setEditingMentionSearchError(null)
      setIsSearchingEditMentions(false)
      setEditingMentionActiveIndex(0)
      return
    }

    let cancelled = false
    setIsSearchingEditMentions(true)
    setEditingMentionSearchError(null)
    const timer = setTimeout(async () => {
      try {
        const results = await onSearchWorkspaceEntries(activeEditMentionMatch.query)
        if (cancelled) {
          return
        }
        setEditingMentionResults(results)
        setEditingMentionActiveIndex(0)
      } catch (error) {
        if (cancelled) {
          return
        }
        setEditingMentionResults([])
        setEditingMentionSearchError(error instanceof Error ? error.message : "Failed to search workspace")
      } finally {
        if (!cancelled) {
          setIsSearchingEditMentions(false)
        }
      }
    }, 120)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeEditMentionMatch, editMentionPopoverOpen, onSearchWorkspaceEntries])

  const selectedModelLabel = useMemo(() => {
    if (isLoadingModels) {
      return "Loading models..."
    }
    return (
      getModelOptionLabel(selectedModel, modelOptionGroups) ??
      (providerError ? "NSBot unavailable" : "No configured providers")
    )
  }, [isLoadingModels, modelOptionGroups, providerError, selectedModel])

  const handleSubmit = async () => {
    if (!canSubmitPrompt || isGenerating || !canCompose || !selectedModel) return
    setIsGenerating(true)
    setComposerBlocks([{ type: "text", text: "" }])
    setSelectionRange({ start: 0, end: 0 })
    setMentionResults([])
    setMentionSearchError(null)
    setDismissedMentionKey(null)
    try {
      await onSendMessage({ blocks: composerBlocks }, { autoAllow: permission === "auto_allow" })
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

  const handleMentionSelection = (mention: WorkspaceEntrySearchResult) => {
    if (!activeMentionMatch) {
      return
    }
    const nextBlocks = replaceComposerBlocksRange(
      composerBlocks,
      activeMentionMatch.start,
      activeMentionMatch.end,
      [{ type: "mention", mention }]
    )
    setComposerBlocks(nextBlocks)
    setSelectionRange({
      start: activeMentionMatch.start + getComposerMentionDisplayText(mention).length,
      end: activeMentionMatch.start + getComposerMentionDisplayText(mention).length,
    })
    setMentionResults([])
    setMentionActiveIndex(0)
    setMentionSearchError(null)
    setDismissedMentionKey(null)
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      const cursor = activeMentionMatch.start + getComposerMentionDisplayText(mention).length
      inputRef.current?.setSelectionRange(cursor, cursor)
    })
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const nextValue = e.target.value
    const diff = getTextareaDiff(inputValue, nextValue)
    setComposerBlocks((current) =>
      replaceComposerBlocksRange(current, diff.start, diff.end, diff.insertedText ? [{ type: "text", text: diff.insertedText }] : [])
    )
    setSelectionRange({
      start: e.target.selectionStart ?? nextValue.length,
      end: e.target.selectionEnd ?? nextValue.length,
    })
  }

  const handleInputSelect = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const nextSelection = snapSelectionOutOfMentionToken(
      composerBlocks,
      e.currentTarget.selectionStart ?? e.currentTarget.value.length,
      e.currentTarget.selectionEnd ?? e.currentTarget.value.length
    )
    setSelectionRange(nextSelection)
    if (
      nextSelection.start !== (e.currentTarget.selectionStart ?? nextSelection.start) ||
      nextSelection.end !== (e.currentTarget.selectionEnd ?? nextSelection.end)
    ) {
      requestAnimationFrame(() => {
        inputRef.current?.setSelectionRange(nextSelection.start, nextSelection.end)
      })
    }
  }

  const handleBeforeInput = (e: React.FormEvent<HTMLTextAreaElement>) => {
    const nativeEvent = e.nativeEvent as InputEvent
    const target = e.currentTarget
    const selectionStart = target.selectionStart ?? selectionRange.start
    const selectionEnd = target.selectionEnd ?? selectionRange.end
    const affectedRange = getAffectedInputRange(
      composerBlocks,
      selectionStart,
      selectionEnd,
      nativeEvent.inputType
    )
    if (!affectedRange) {
      return
    }

    e.preventDefault()
    const replacementText = getBeforeInputReplacementText(nativeEvent)
    const nextBlocks = replaceComposerBlocksRange(
      composerBlocks,
      affectedRange.start,
      affectedRange.end,
      replacementText ? [{ type: "text", text: replacementText }] : []
    )
    const nextCursor = affectedRange.start + replacementText.length
    setComposerBlocks(nextBlocks)
    setSelectionRange({ start: nextCursor, end: nextCursor })
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      inputRef.current?.setSelectionRange(nextCursor, nextCursor)
    })
  }

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const selectionStart = e.currentTarget.selectionStart ?? selectionRange.start
    const selectionEnd = e.currentTarget.selectionEnd ?? selectionRange.end
    const tokenRanges = getComposerMentionRanges(composerBlocks)
    const overlapsToken = tokenRanges.some(
      (range) => selectionStart < range.end && selectionEnd > range.start
    )
    if (!overlapsToken) {
      return
    }

    e.preventDefault()
    const pastedText = e.clipboardData.getData("text")
    const nextBlocks = replaceComposerBlocksRange(
      composerBlocks,
      selectionStart,
      selectionEnd,
      pastedText ? [{ type: "text", text: pastedText }] : []
    )
    const nextCursor = selectionStart + pastedText.length
    setComposerBlocks(nextBlocks)
    setSelectionRange({ start: nextCursor, end: nextCursor })
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      inputRef.current?.setSelectionRange(nextCursor, nextCursor)
    })
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionPopoverOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setMentionActiveIndex((current) =>
          mentionResults.length === 0 ? 0 : (current + 1) % mentionResults.length
        )
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setMentionActiveIndex((current) =>
          mentionResults.length === 0 ? 0 : (current - 1 + mentionResults.length) % mentionResults.length
        )
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        setDismissedMentionKey(activeMentionKey)
        setMentionResults([])
        setMentionSearchError(null)
        setIsSearchingMentions(false)
        return
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault()
        if (mentionResults.length > 0) {
          handleMentionSelection(mentionResults[mentionActiveIndex] ?? mentionResults[0])
        }
        return
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  const handleEditMentionSelection = (mention: WorkspaceEntrySearchResult) => {
    if (!activeEditMentionMatch) {
      return
    }
    const nextBlocks = replaceComposerBlocksRange(
      editingBlocks,
      activeEditMentionMatch.start,
      activeEditMentionMatch.end,
      [{ type: "mention", mention }]
    )
    setEditingBlocks(nextBlocks)
    const cursor = activeEditMentionMatch.start + getComposerMentionDisplayText(mention).length
    setEditingSelectionRange({ start: cursor, end: cursor })
    setEditingMentionResults([])
    setEditingMentionActiveIndex(0)
    setEditingMentionSearchError(null)
    setDismissedEditMentionKey(null)
    requestAnimationFrame(() => {
      editingInputRef.current?.focus()
      editingInputRef.current?.setSelectionRange(cursor, cursor)
    })
  }

  const handleEditInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const nextValue = e.target.value
    const diff = getTextareaDiff(editingValue, nextValue)
    setEditingBlocks((current) =>
      replaceComposerBlocksRange(current, diff.start, diff.end, diff.insertedText ? [{ type: "text", text: diff.insertedText }] : [])
    )
    setEditingSelectionRange({
      start: e.target.selectionStart ?? nextValue.length,
      end: e.target.selectionEnd ?? nextValue.length,
    })
  }

  const handleEditInputSelect = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const nextSelection = snapSelectionOutOfMentionToken(
      editingBlocks,
      e.currentTarget.selectionStart ?? editingSelectionRange.start,
      e.currentTarget.selectionEnd ?? editingSelectionRange.end
    )
    setEditingSelectionRange(nextSelection)
    if (
      nextSelection.start !== (e.currentTarget.selectionStart ?? nextSelection.start) ||
      nextSelection.end !== (e.currentTarget.selectionEnd ?? nextSelection.end)
    ) {
      requestAnimationFrame(() => {
        editingInputRef.current?.setSelectionRange(nextSelection.start, nextSelection.end)
      })
    }
  }

  const handleEditBeforeInput = (e: React.FormEvent<HTMLTextAreaElement>) => {
    const nativeEvent = e.nativeEvent as InputEvent
    const target = e.currentTarget
    const selectionStart = target.selectionStart ?? editingSelectionRange.start
    const selectionEnd = target.selectionEnd ?? editingSelectionRange.end
    const affectedRange = getAffectedInputRange(
      editingBlocks,
      selectionStart,
      selectionEnd,
      nativeEvent.inputType
    )
    if (!affectedRange) {
      return
    }

    e.preventDefault()
    const replacementText = getBeforeInputReplacementText(nativeEvent)
    const nextBlocks = replaceComposerBlocksRange(
      editingBlocks,
      affectedRange.start,
      affectedRange.end,
      replacementText ? [{ type: "text", text: replacementText }] : []
    )
    const nextCursor = affectedRange.start + replacementText.length
    setEditingBlocks(nextBlocks)
    setEditingSelectionRange({ start: nextCursor, end: nextCursor })
    requestAnimationFrame(() => {
      editingInputRef.current?.focus()
      editingInputRef.current?.setSelectionRange(nextCursor, nextCursor)
    })
  }

  const handleEditPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const selectionStart = e.currentTarget.selectionStart ?? editingSelectionRange.start
    const selectionEnd = e.currentTarget.selectionEnd ?? editingSelectionRange.end
    const tokenRanges = getComposerMentionRanges(editingBlocks)
    const overlapsToken = tokenRanges.some(
      (range) => selectionStart < range.end && selectionEnd > range.start
    )
    if (!overlapsToken) {
      return
    }

    e.preventDefault()
    const pastedText = e.clipboardData.getData("text")
    const nextBlocks = replaceComposerBlocksRange(
      editingBlocks,
      selectionStart,
      selectionEnd,
      pastedText ? [{ type: "text", text: pastedText }] : []
    )
    const nextCursor = selectionStart + pastedText.length
    setEditingBlocks(nextBlocks)
    setEditingSelectionRange({ start: nextCursor, end: nextCursor })
    requestAnimationFrame(() => {
      editingInputRef.current?.focus()
      editingInputRef.current?.setSelectionRange(nextCursor, nextCursor)
    })
  }

  const handleEditKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (editMentionPopoverOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setEditingMentionActiveIndex((current) =>
          editingMentionResults.length === 0 ? 0 : (current + 1) % editingMentionResults.length
        )
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setEditingMentionActiveIndex((current) =>
          editingMentionResults.length === 0 ? 0 : (current - 1 + editingMentionResults.length) % editingMentionResults.length
        )
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        setDismissedEditMentionKey(activeEditMentionKey)
        setEditingMentionResults([])
        setEditingMentionSearchError(null)
        setIsSearchingEditMentions(false)
        return
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault()
        if (editingMentionResults.length > 0) {
          handleEditMentionSelection(editingMentionResults[editingMentionActiveIndex] ?? editingMentionResults[0])
        }
        return
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void submitEditedConversationEvent()
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

  const startEditConversationEvent = (entryId: string, content: string) => {
    if (isSubmittingEdit) {
      return
    }
    setEditingEntryId(entryId)
    const entry = messages.find((item) => item.id === entryId)
    const nextBlocks = buildComposerBlocksFromPromptBlocks(entry?.promptBlocks)
    setEditingBlocks(nextBlocks)
    const cursor = serializeComposerBlocks(nextBlocks).length
    setEditingSelectionRange({ start: cursor, end: cursor })
  }

  const cancelEditConversationEvent = () => {
    if (isSubmittingEdit) {
      return
    }
    setEditingEntryId(null)
    setEditingBlocks([{ type: "text", text: "" }])
    setEditingSelectionRange({ start: 0, end: 0 })
    setEditingMentionResults([])
    setEditingMentionSearchError(null)
  }

  const submitEditedConversationEvent = async () => {
    if (!editingEntryId || isSubmittingEdit) {
      return
    }
    if (stripInlineTokenText(editingValue).trim().length === 0 && editingSelectedMentions.length === 0) {
      return
    }
    setIsSubmittingEdit(true)
    try {
      await onEditConversationEventAndRerun(editingEntryId, editingBlocks, {
        autoAllow: permission === "auto_allow",
      })
      setEditingEntryId(null)
      setEditingBlocks([{ type: "text", text: "" }])
      setEditingSelectionRange({ start: 0, end: 0 })
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
  const normalizedMessages = useMemo(
    () =>
      normalizeConversationMessages({
        timelineEvents: messages,
        liveTurn,
        pendingPermissionRequest,
      }),
    [liveTurn, messages, pendingPermissionRequest]
  )

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

      <ConversationStream
        hasMessages={hasMessages}
        activeProjectLabel={activeProjectLabel}
        hasMoreHistory={hasMoreHistory}
        isLoadingHistory={isLoadingHistory}
        onLoadEarlierTimeline={onLoadEarlierTimeline}
        messages={normalizedMessages}
        onAllowPermissionRequest={onAllowPermissionRequest}
        onAllowAlwaysPermissionRequest={() => {
          setPermission("auto_allow")
          onAllowAlwaysPermissionRequest()
        }}
        onRejectPermissionRequest={onRejectPermissionRequest}
        onRejectAlwaysPermissionRequest={onRejectAlwaysPermissionRequest}
        onCancelPermissionRequest={onCancelPermissionRequest}
        renderConversationEvent={(entry) => (
          <ConversationEventView
            key={entry.id}
            entry={entry}
            showRunningIndicator={isTurnPending && entry.entryKind === "action" && entry.id === latestCurrentTurnActionId}
            isEditing={editingEntryId === entry.id}
            editingBlocks={editingBlocks}
            isSubmittingEdit={isSubmittingEdit}
            copied={copiedEntryId === entry.id}
            onCopyMessage={(entryId, content) => {
              void copyMessage(entryId, content)
            }}
            editingInputRef={editingInputRef}
            editingMentionResults={editingMentionResults}
            editingMentionActiveIndex={editingMentionActiveIndex}
            isSearchingEditMentions={isSearchingEditMentions}
            editingMentionSearchError={editingMentionSearchError}
            editMentionPopoverOpen={editMentionPopoverOpen}
            activeEditMentionQuery={activeEditMentionMatch?.query ?? ""}
            onEditInputChange={handleEditInputChange}
            onEditInputSelect={handleEditInputSelect}
            onEditBeforeInput={handleEditBeforeInput}
            onEditPaste={handleEditPaste}
            onEditKeyDown={handleEditKeyDown}
            onEditMentionSelect={handleEditMentionSelection}
            onStartEdit={startEditConversationEvent}
            onCancelEdit={cancelEditConversationEvent}
            onSubmitEdit={() => {
              void submitEditedConversationEvent()
            }}
            canEditUserMessage={
              entry.entryKind === "user_input" && entry.id === latestEditableUserMessageId
            }
          />
        )}
        renderLivePlanningEntry={(entry) => <LivePlanningStepCard key={entry.id} entry={entry} />}
        showPreStepTurnLoading={showPreStepTurnLoading}
        showGenerating={isGenerating}
        generatingIndicator={
          <div className="flex gap-3 items-start">
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
            <div className="bg-[#faf8f6] border border-[#e8e4e0] rounded-2xl px-4 py-3">
              <ThinkingDots />
            </div>
          </div>
        }
      />

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

            {mentionPopoverOpen && (
              <div className="mb-3" data-testid="file-mention-popover-stack">
                <FileMentionPopover
                  query={activeMentionMatch?.query ?? ""}
                  results={mentionResults}
                  activeIndex={mentionActiveIndex}
                  isLoading={isSearchingMentions}
                  error={mentionSearchError}
                  onSelect={handleMentionSelection}
                />
                <MentionAssistText />
              </div>
            )}

            <div className="relative">
              {inputValue.length > 0 && (
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-0 whitespace-pre-wrap break-words text-sm leading-6 text-foreground"
                >
                  <ComposerInlinePreview blocks={composerBlocks} />
                </div>
              )}
              <textarea
                ref={inputRef}
                value={inputValue}
                onBeforeInput={handleBeforeInput}
                onPaste={handlePaste}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                onClick={handleInputSelect}
                onSelect={handleInputSelect}
                placeholder={canCompose ? "Ask for follow-up changes" : "Select or create a session first"}
                disabled={!canCompose}
                aria-keyshortcuts="Enter"
                rows={2}
                className="relative min-h-[52px] w-full resize-none bg-transparent text-sm leading-6 text-transparent caret-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>
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

          {turnError && <div className="px-4 pb-2 text-xs text-[#b45b44]">{turnError}</div>}

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
                    void onCancelTurn?.()
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
                        disabled={!canSubmitPrompt || !canCompose || !selectedModel}
                        className={cn(
                          "p-2 rounded-full transition-colors",
                          canSubmitPrompt && canCompose && selectedModel
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
      </div>
    </main>
  )
}

function FileMentionPopover({
  query,
  results,
  activeIndex,
  isLoading,
  error,
  onSelect,
}: {
  query: string
  results: WorkspaceEntrySearchResult[]
  activeIndex: number
  isLoading: boolean
  error: string | null
  onSelect: (entry: WorkspaceEntrySearchResult) => void
}) {
  return (
    <div className="rounded-xl border border-[#e1ddd8] bg-[#fbfaf8] shadow-sm">
      <div className="flex items-center justify-between border-b border-[#ede7e1] px-3 py-2 text-[11px] uppercase tracking-[0.12em] text-foreground/45">
        <span>Files</span>
        <span>@{query}</span>
      </div>
      <div className="max-h-64 overflow-y-auto p-1">
        {isLoading ? (
          <div className="px-3 py-3 text-sm text-muted-foreground">Searching workspace...</div>
        ) : error ? (
          <div className="px-3 py-3 text-sm text-[#b45b44]">{error}</div>
        ) : results.length === 0 ? (
          <div className="px-3 py-3 text-sm text-muted-foreground">No files or directories match this query.</div>
        ) : (
          results.map((result, index) => (
            <button
              key={result.absolutePath}
              type="button"
              onClick={() => onSelect(result)}
              className={cn(
                "flex w-full items-start justify-between gap-3 rounded-lg px-3 py-2 text-left transition-colors",
                index === activeIndex ? "bg-[#efe9e4]" : "hover:bg-[#f4efea]"
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-foreground/85">{result.name}</div>
                {result.parentPath ? (
                  <div className="truncate text-xs text-muted-foreground">{result.parentPath}</div>
                ) : null}
              </div>
              <span className="rounded-full bg-[#f1ede8] px-2 py-0.5 text-[11px] text-foreground/55">
                {result.entryType === "directory" ? "Dir" : "File"}
              </span>
            </button>
          ))
        )}
      </div>
    </div>
  )
}

function MentionAssistText() {
  return (
    <div className="pt-2 text-[12px] text-muted-foreground">
      Use ↑/↓ to navigate, Enter or Tab to insert, Esc to close.
    </div>
  )
}

function ComposerInlinePreview({ blocks }: { blocks: ComposerInlineBlock[] }) {
  return (
    <>
      {blocks.map((block, index) =>
        block.type === "text" ? (
          <span key={`composer-text:${index}`}>{block.text}</span>
        ) : (
          <span
            key={`composer-mention:${index}:${block.mention.absolutePath}`}
            className="rounded-[4px] bg-[#edf3ef] text-[#365444] outline outline-1 outline-[#cfded3]"
          >
            {getComposerMentionDisplayText(block.mention)}
          </span>
        )
      )}
    </>
  )
}

function getComposerMentionDisplayText(mention: WorkspaceEntrySearchResult): string {
  return `@${mention.name}`
}

function serializeComposerBlocks(blocks: ComposerInlineBlock[]): string {
  return blocks
    .map((block) =>
      block.type === "text" ? block.text : getComposerMentionDisplayText(block.mention)
    )
    .join("")
}

function getComposerMentionRanges(
  blocks: ComposerInlineBlock[]
): Array<{ start: number; end: number; index: number }> {
  const ranges: Array<{ start: number; end: number; index: number }> = []
  let cursor = 0
  let mentionIndex = 0

  for (const block of blocks) {
    const text = block.type === "text" ? block.text : getComposerMentionDisplayText(block.mention)
    const nextCursor = cursor + text.length
    if (block.type === "mention") {
      ranges.push({ start: cursor, end: nextCursor, index: mentionIndex })
      mentionIndex += 1
    }
    cursor = nextCursor
  }

  return ranges
}

function snapSelectionOutOfMentionToken(
  blocks: ComposerInlineBlock[],
  start: number,
  end: number
): { start: number; end: number } {
  if (start !== end) {
    return { start, end }
  }

  for (const range of getComposerMentionRanges(blocks)) {
    if (start <= range.start || start >= range.end) {
      continue
    }
    const midpoint = range.start + (range.end - range.start) / 2
    const snapped = start < midpoint ? range.start : range.end
    return { start: snapped, end: snapped }
  }

  return { start, end }
}

function getAffectedInputRange(
  blocks: ComposerInlineBlock[],
  start: number,
  end: number,
  inputType: string
): { start: number; end: number } | null {
  const tokenRanges = getComposerMentionRanges(blocks)
  if (start !== end) {
    const overlaps = tokenRanges.some((range) => start < range.end && end > range.start)
    return overlaps ? { start, end } : null
  }

  if (inputType === "deleteContentBackward") {
    const touching = tokenRanges.find((range) => range.end === start)
    return touching ? { start: touching.start, end: touching.end } : null
  }

  if (inputType === "deleteContentForward") {
    const touching = tokenRanges.find((range) => range.start === start)
    return touching ? { start: touching.start, end: touching.end } : null
  }

  const inside = tokenRanges.find((range) => start > range.start && start < range.end)
  return inside ? { start: inside.start, end: inside.end } : null
}

function getBeforeInputReplacementText(event: InputEvent): string {
  if (event.inputType === "insertParagraph" || event.inputType === "insertLineBreak") {
    return "\n"
  }
  return typeof event.data === "string" ? event.data.replaceAll(INLINE_MENTION_SENTINEL, "") : ""
}

function getTextareaDiff(previous: string, next: string): {
  start: number
  end: number
  insertedText: string
} {
  let start = 0
  while (start < previous.length && start < next.length && previous[start] === next[start]) {
    start += 1
  }

  let previousEnd = previous.length
  let nextEnd = next.length
  while (previousEnd > start && nextEnd > start && previous[previousEnd - 1] === next[nextEnd - 1]) {
    previousEnd -= 1
    nextEnd -= 1
  }

  return {
    start,
    end: previousEnd,
    insertedText: next.slice(start, nextEnd),
  }
}

function replaceComposerBlocksRange(
  blocks: ComposerInlineBlock[],
  start: number,
  end: number,
  replacementBlocks: ComposerInlineBlock[]
): ComposerInlineBlock[] {
  const nextBlocks: ComposerInlineBlock[] = []
  let cursor = 0
  let inserted = false

  const pushText = (text: string) => {
    if (!text) {
      return
    }
    const previous = nextBlocks[nextBlocks.length - 1]
    if (previous?.type === "text") {
      previous.text += text
      return
    }
    nextBlocks.push({ type: "text", text })
  }

  const pushReplacement = () => {
    if (inserted) {
      return
    }
    for (const block of replacementBlocks) {
      if (block.type === "text") {
        pushText(block.text)
      } else {
        nextBlocks.push(block)
      }
    }
    inserted = true
  }

  for (const block of blocks) {
    const blockText = block.type === "text" ? block.text : getComposerMentionDisplayText(block.mention)
    const blockStart = cursor
    const blockEnd = cursor + blockText.length

    if (blockEnd <= start) {
      if (block.type === "text") {
        pushText(block.text)
      } else {
        nextBlocks.push(block)
      }
      cursor = blockEnd
      continue
    }

    if (blockStart >= end) {
      pushReplacement()
      if (block.type === "text") {
        pushText(block.text)
      } else {
        nextBlocks.push(block)
      }
      cursor = blockEnd
      continue
    }

    if (block.type === "text") {
      const localStart = Math.max(0, start - blockStart)
      const localEnd = Math.max(0, end - blockStart)
      pushText(block.text.slice(0, localStart))
      pushReplacement()
      pushText(block.text.slice(localEnd))
    } else {
      pushReplacement()
    }

    cursor = blockEnd
  }

  pushReplacement()
  if (nextBlocks.length === 0 || nextBlocks.every((block) => block.type !== "text")) {
    nextBlocks.push({ type: "text", text: "" })
  }

  return nextBlocks
}

function stripInlineTokenText(value: string): string {
  return value.replaceAll(INLINE_MENTION_SENTINEL, "")
}

function getActiveMentionMatch(
  value: string,
  cursorPosition: number
): { query: string; start: number; end: number } | null {
  const textBeforeCursor = value.slice(0, cursorPosition)
  const match = textBeforeCursor.match(/(^|\s)@([^\s@]{1,120})$/)
  if (!match) {
    return null
  }

  const query = match[2]
  const end = textBeforeCursor.length
  const start = end - query.length - 1
  if (query.trim().length < 1) {
    return null
  }

  return { query, start, end }
}

function ConversationEventView({
  entry,
  showRunningIndicator,
  copied,
  isEditing,
  editingBlocks,
  isSubmittingEdit,
  onCopyMessage,
  editingInputRef,
  editingMentionResults,
  editingMentionActiveIndex,
  isSearchingEditMentions,
  editingMentionSearchError,
  editMentionPopoverOpen,
  activeEditMentionQuery,
  onEditInputChange,
  onEditInputSelect,
  onEditBeforeInput,
  onEditPaste,
  onEditKeyDown,
  onEditMentionSelect,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
  canEditUserMessage,
}: {
  entry: ConversationEvent
  showRunningIndicator: boolean
  copied: boolean
  isEditing: boolean
  editingBlocks: ComposerInlineBlock[]
  isSubmittingEdit: boolean
  onCopyMessage: (entryId: string, content: string) => void
  editingInputRef: React.RefObject<HTMLTextAreaElement | null>
  editingMentionResults: WorkspaceEntrySearchResult[]
  editingMentionActiveIndex: number
  isSearchingEditMentions: boolean
  editingMentionSearchError: string | null
  editMentionPopoverOpen: boolean
  activeEditMentionQuery: string
  onEditInputChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => void
  onEditInputSelect: (event: React.SyntheticEvent<HTMLTextAreaElement>) => void
  onEditBeforeInput: (event: React.FormEvent<HTMLTextAreaElement>) => void
  onEditPaste: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void
  onEditKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onEditMentionSelect: (entry: WorkspaceEntrySearchResult) => void
  onStartEdit: (messageId: string, content: string) => void
  onCancelEdit: () => void
  onSubmitEdit: () => void
  canEditUserMessage: boolean
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
              <div className="relative">
                {serializeComposerBlocks(editingBlocks).length > 0 ? (
                  <div
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-0 whitespace-pre-wrap break-words text-sm leading-6 text-foreground/90"
                  >
                    <ComposerInlinePreview blocks={editingBlocks} />
                  </div>
                ) : null}
                <textarea
                  ref={editingInputRef}
                  value={serializeComposerBlocks(editingBlocks)}
                  onBeforeInput={onEditBeforeInput}
                  onPaste={onEditPaste}
                  onChange={onEditInputChange}
                  onKeyDown={onEditKeyDown}
                  onClick={onEditInputSelect}
                  onSelect={onEditInputSelect}
                  disabled={isSubmittingEdit}
                  rows={5}
                  className="w-full resize-none bg-transparent text-sm leading-6 text-transparent caret-foreground focus:outline-none disabled:opacity-60"
                />
              </div>
              {editMentionPopoverOpen ? (
                <div className="mt-3">
                  <FileMentionPopover
                    query={activeEditMentionQuery}
                    results={editingMentionResults}
                    activeIndex={editingMentionActiveIndex}
                    isLoading={isSearchingEditMentions}
                    error={editingMentionSearchError}
                    onSelect={onEditMentionSelect}
                  />
                  <MentionAssistText />
                </div>
              ) : null}
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
                  disabled={
                    isSubmittingEdit ||
                    (stripInlineTokenText(serializeComposerBlocks(editingBlocks)).trim().length === 0 &&
                      !editingBlocks.some((block) => block.type === "mention"))
                  }
                  className="rounded-2xl bg-foreground px-4 py-1.5 text-sm text-background hover:bg-foreground/85 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isSubmittingEdit ? "Sending..." : "Send"}
                </button>
              </div>
            </div>
          ) : (
            <div className="w-full bg-[#f0ebe6] border border-[#e8e4e0] rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm text-foreground/90 whitespace-pre-wrap">
              <UserMessageContent entry={entry} />
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
              {canEditUserMessage ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => onStartEdit(entry.id, entry.editableText ?? entry.contentText ?? "")}
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
              ) : null}
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

function UserMessageContent({ entry }: { entry: ConversationEvent }) {
  if (!entry.displayBlocks || entry.displayBlocks.length === 0) {
    return <>{entry.contentText}</>
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      {entry.displayBlocks.map((block, index) =>
        block.type === "text" ? (
          <span key={`${entry.id}:text:${index}`} className="whitespace-pre-wrap">
            {block.text}
          </span>
        ) : (
          <span
            key={`${entry.id}:resource:${index}`}
            className="inline-flex items-center rounded-md border border-[#d8cfc7] bg-[#f7f2ed] px-2 py-0.5 text-[12.5px] font-medium text-[#7a4a2f] underline decoration-[#c59577] underline-offset-2"
          >
            {block.label}
          </span>
        )
      )}
    </span>
  )
}

function PlanningStepCard({ step }: { step: PlanningEntry }) {
  return (
    <div className="pl-1">
      <AgentMessageContent content={step.contentText ?? ""} />
    </div>
  )
}

function LivePlanningStepCard({ entry }: { entry: LiveTurn["planEntries"][number] }) {
  return (
    <div className="space-y-1 pl-1">
      <div className="flex items-center gap-2 text-xs text-foreground/45">
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
    <div className="space-y-2 pl-1">
      <div className="text-xs text-foreground/45">Step {step.stepNumber}</div>
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
          label="View Steps"
          open={showToolCallsPanel}
          onToggle={() => setShowToolCallsPanel((current) => !current)}
        >
          <div className="space-y-1 text-sm text-foreground/70">
            {visibleToolCalls.map((toolCall, index) => (
              <div
                key={`${toolCall.name}-${index}`}
                className="flex items-center gap-2 whitespace-nowrap overflow-hidden"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-[#5aa35a] flex-shrink-0" />
                <span className="truncate">
                  {toolCall.name} {toolCall.argumentsText}
                </span>
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
        className="inline-flex items-center gap-1 text-sm text-foreground/55 hover:text-foreground/75 transition-colors"
      >
        <span>{open ? "v" : ">"}</span>
        <span>{label}</span>
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
