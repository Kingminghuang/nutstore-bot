"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  Mic,
  Paperclip,
  Plus,
  ShieldCheck,
  Square,
  X,
} from "lucide-react"

import type { Message, Project, Session } from "@/app/page"
import {
  getModelOptionLabel,
  type ModelOptionGroup,
  type SelectedModelRef,
} from "@/lib/provider-settings"
import { cn } from "@/lib/utils"

type Permission = "default" | "full"

const EMPTY_MESSAGES: Message[] = []

interface MainContentProps {
  activeProject: Project | null
  activeSession: Session | null
  onSendMessage: (text: string) => Promise<void>
  modelOptionGroups: ModelOptionGroup[]
  selectedModel: SelectedModelRef | null
  onSelectedModelChange: (value: SelectedModelRef | null) => void
  isLoadingModels: boolean
  providerError: string | null
  runError: string | null
}

export function MainContent({
  activeProject,
  activeSession,
  onSendMessage,
  modelOptionGroups,
  selectedModel,
  onSelectedModelChange,
  isLoadingModels,
  providerError,
  runError,
}: MainContentProps) {
  const [permissionOpen, setPermissionOpen] = useState(false)
  const [permission, setPermission] = useState<Permission>("default")
  const [modelOpen, setModelOpen] = useState(false)
  const [inputValue, setInputValue] = useState("")
  const [isGenerating, setIsGenerating] = useState(false)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const messages = activeSession?.messages ?? EMPTY_MESSAGES
  const hasMessages = messages.length > 0
  const hasAvailableModels = modelOptionGroups.some((group) => group.models.length > 0)

  const selectedModelLabel = useMemo(() => {
    if (isLoadingModels) {
      return "Loading models..."
    }

    return (
      getModelOptionLabel(selectedModel, modelOptionGroups) ??
      (providerError ? "Sidecar unavailable" : "No configured providers")
    )
  }, [isLoadingModels, modelOptionGroups, providerError, selectedModel])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

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
    if (!text || isGenerating || !activeSession || !selectedModel) return

    setIsGenerating(true)

    try {
      await onSendMessage(text)
      setInputValue("")
      setSelectedFiles([])
    } catch {
      // Error state is surfaced by the page container.
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
    setSelectedFiles((prev) => {
      const existingNames = new Set(prev.map((file) => file.name))
      const newFiles = files.filter((file) => !existingNames.has(file.name))
      return [...prev, ...newFiles]
    })
    e.target.value = ""
  }

  const removeFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, currentIndex) => currentIndex !== index))
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  const permissionOptions: { value: Permission; label: string }[] = [
    { value: "default", label: "Default permissions" },
    { value: "full", label: "Full access" },
  ]

  const activeSessionLabel = activeSession?.title ?? "New session"
  const activeProjectLabel = activeProject?.name ?? ""

  return (
    <main
      className="flex-1 flex flex-col bg-background h-screen"
      onClick={() => {
        setModelOpen(false)
        setPermissionOpen(false)
      }}
    >
      <header className="h-14 border-b border-border flex items-center px-6 flex-shrink-0">
        <h1 className="text-sm font-medium">{activeSessionLabel}</h1>
      </header>

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto relative"
      >
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
            {messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))}
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
          className="bg-[#faf8f6] border border-[#e8e4e0] rounded-2xl relative"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="p-4">
            {selectedFiles.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-3">
                {selectedFiles.map((file, index) => (
                  <div
                    key={index}
                    className="flex items-center gap-1 bg-[#efe9e4] border border-[#e0d9d2] rounded-lg px-2 py-1 text-xs text-foreground/70 max-w-[180px]"
                  >
                    <Paperclip className="w-3 h-3 flex-shrink-0 text-muted-foreground" />
                    <span className="truncate" title={file.name}>{file.name}</span>
                    <button
                      onClick={() => removeFile(index)}
                      className="ml-0.5 flex-shrink-0 hover:text-foreground/90 transition-colors"
                      aria-label={`Remove ${file.name}`}
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
              placeholder={activeSession ? "Ask for follow-up changes" : "Select or create a session first"}
              disabled={!activeSession}
              aria-keyshortcuts="Enter"
              className="w-full bg-transparent text-sm placeholder:text-muted-foreground focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            />
          </div>

          {(providerError || (!hasAvailableModels && !isLoadingModels)) && (
            <div className="px-4 pb-1 text-xs text-muted-foreground">
              {providerError ?? "Configure a provider in Settings to enable model selection."}
            </div>
          )}

          {runError && (
            <div className="px-4 pb-2 text-xs text-[#b45b44]">{runError}</div>
          )}

          <div className="flex items-center justify-between px-4 pb-3">
            <div className="flex items-center gap-4">
              <button
                onClick={handleFilePickerClick}
                disabled={!activeProject}
                title={activeProject ? `Pick files from ${activeProject.path}` : "Select a project first"}
                className={cn(
                  "p-1.5 rounded-lg transition-colors",
                  activeProject
                    ? "hover:bg-[#efe9e4] text-muted-foreground hover:text-foreground"
                    : "text-muted-foreground/40 cursor-not-allowed"
                )}
                aria-label="Attach files from project folder"
              >
                <Plus className="w-4 h-4" />
              </button>

              <div className="relative">
                <button
                  onClick={() => {
                    if (!hasAvailableModels || isLoadingModels) return
                    setModelOpen(!modelOpen)
                    setPermissionOpen(false)
                  }}
                  disabled={!hasAvailableModels || isLoadingModels}
                  className={cn(
                    "flex items-center gap-1.5 text-sm transition-colors",
                    hasAvailableModels && !isLoadingModels
                      ? "text-muted-foreground hover:text-foreground"
                      : "text-muted-foreground/50 cursor-not-allowed"
                  )}
                >
                  <span className="max-w-[260px] truncate">{selectedModelLabel}</span>
                  <ChevronDown className="w-3.5 h-3.5" />
                </button>
                {modelOpen && (
                  <div className="absolute bottom-full mb-2 left-0 bg-background border border-[#e8e4e0] rounded-xl shadow-md overflow-hidden z-20 w-80 max-h-80 overflow-y-auto">
                    <div className="px-4 py-2 text-xs text-muted-foreground">Select model</div>
                    {modelOptionGroups.map((group) => (
                      <div key={group.connectionId} className="border-t border-[#f0ebe6] first:border-t-0">
                        <div className="px-4 py-2 text-[11px] uppercase tracking-[0.12em] text-foreground/45">
                          {group.providerLabel}
                        </div>
                        {group.models.map((model) => {
                          const isSelected =
                            selectedModel?.connectionId === group.connectionId &&
                            selectedModel.modelId === model.modelId

                          return (
                            <button
                              key={`${group.connectionId}:${model.modelId}`}
                              onClick={() => {
                                onSelectedModelChange({
                                  connectionId: group.connectionId,
                                  modelId: model.modelId,
                                })
                                setModelOpen(false)
                              }}
                              className="w-full flex items-center justify-between px-4 py-2 text-sm text-foreground/80 hover:bg-[#efe9e4] transition-colors"
                            >
                              <span className="truncate">{model.label}</span>
                              {isSelected && (
                                <Check className="w-4 h-4 text-foreground/70 flex-shrink-0" />
                              )}
                            </button>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="relative">
                <button
                  onClick={() => {
                    setPermissionOpen(!permissionOpen)
                    setModelOpen(false)
                  }}
                  className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                  <ShieldCheck className="w-4 h-4" />
                  {permission === "default" ? "Default permissions" : "Full access"}
                  <ChevronDown className="w-3.5 h-3.5" />
                </button>
                {permissionOpen && (
                  <div className="absolute bottom-full mb-2 left-0 bg-background border border-[#e8e4e0] rounded-xl shadow-md overflow-hidden z-20 w-52">
                    {permissionOptions.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={() => {
                          setPermission(opt.value)
                          setPermissionOpen(false)
                        }}
                        className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-foreground/80 hover:bg-[#efe9e4] transition-colors"
                      >
                        <ShieldCheck className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                        <span className="flex-1 text-left">{opt.label}</span>
                        {permission === opt.value && (
                          <Check className="w-4 h-4 text-foreground/70 flex-shrink-0" />
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button className="p-1.5 hover:bg-[#efe9e4] rounded-lg transition-colors">
                <Mic className="w-4 h-4 text-muted-foreground" />
              </button>

              {isGenerating ? (
                <button
                  onClick={() => setIsGenerating(false)}
                  className="p-2 bg-foreground hover:bg-foreground/80 text-background rounded-full transition-colors"
                  aria-label="Stop"
                >
                  <Square className="w-4 h-4" />
                </button>
              ) : (
                <button
                  onClick={() => {
                    void handleSubmit()
                  }}
                  disabled={!inputValue.trim() || !activeSession || !selectedModel}
                  title={inputValue.trim() && activeSession && selectedModel ? "Send (Enter)" : undefined}
                  className={cn(
                    "p-2 rounded-full transition-colors",
                    inputValue.trim() && activeSession && selectedModel
                      ? "bg-[#f5a76c] hover:bg-[#e99a5f] text-white"
                      : "bg-[#e8e4e0] text-muted-foreground cursor-not-allowed"
                  )}
                  aria-label="Send"
                  aria-keyshortcuts="Enter"
                >
                  <ArrowUp className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}

function MessageBubble({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-[#f0ebe6] border border-[#e8e4e0] rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm text-foreground/90 whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-3 items-start">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#e87b5f] to-[#8bc28f] flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <AgentMessageContent content={message.content} />
      </div>
    </div>
  )
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
