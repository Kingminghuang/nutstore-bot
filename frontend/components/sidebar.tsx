"use client"

import { useEffect, useRef, useState } from "react"
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Edit,
  Folder,
  FolderPlus,
  PencilLine,
  Settings,
  SquarePen,
  Trash2,
} from "lucide-react"

import type { Project, Session } from "@/app/page"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface SidebarProps {
  projects: Project[]
  activeProjectId: string | null
  activeSessionId: string | null
  width: number
  onAddProject: (name: string, path: string) => void | Promise<void>
  onRenameProject: (projectId: string, name: string, pathLabel?: string) => void | Promise<void>
  onNewSession: (projectId?: string) => void | Promise<void>
  onSessionChange: (sessionId: string, projectId: string) => void
  onRemoveProject: (projectId: string) => void | Promise<void>
  onSettingsOpen?: () => void
  onResizeStart: (e: React.MouseEvent) => void
}

type WorkspaceDraft = {
  name: string
  path: string
}

type ProductShellWorkspaceSelection = {
  name?: string
  realPath: string
  pathLabel?: string
}

type DirectoryPickerSelection = {
  name?: string
  path?: string
  fullPath?: string
  realPath?: string
  pathLabel?: string
}

type DirectoryPicker = () => Promise<DirectoryPickerSelection>

type ProductShellBridge = {
  pickWorkspaceDirectory?: () => Promise<ProductShellWorkspaceSelection>
  window?: {
    showDirectoryPicker?: DirectoryPicker
  }
}

const SESSION_LIMIT = 5

export function Sidebar({
  projects,
  activeProjectId,
  activeSessionId,
  width,
  onAddProject,
  onRenameProject,
  onNewSession,
  onSessionChange,
  onRemoveProject,
  onSettingsOpen,
  onResizeStart,
}: SidebarProps) {
  const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false)
  const [workspaceDialogError, setWorkspaceDialogError] = useState<string | null>(null)
  const [workspaceDialogSubmitting, setWorkspaceDialogSubmitting] = useState(false)
  const [forceManualWorkspaceEntry, setForceManualWorkspaceEntry] = useState(false)
  const [workspaceDraft, setWorkspaceDraft] = useState<WorkspaceDraft>({ name: "", path: "" })

  const productShellBridge =
    typeof window === "undefined"
      ? null
      : ((window as Window & { __NSBOT_SHELL__?: ProductShellBridge }).__NSBOT_SHELL__ ?? null)
  const resolvedDirectoryPicker = resolveDirectoryPicker(productShellBridge)
  const canUseDirectoryPicker = resolvedDirectoryPicker !== null && !forceManualWorkspaceEntry
  const isManualWorkspaceEntry = !canUseDirectoryPicker
  const manualEntryHint =
    resolvedDirectoryPicker === null
      ? "This environment doesn't support direct folder selection. Please enter the directory name and path manually."
      : "Please enter the directory name and path manually."

  const handleAddProjectClick = () => {
    setForceManualWorkspaceEntry(false)
    setWorkspaceDraft({ name: "", path: "" })
    setWorkspaceDialogError(null)
    setWorkspaceDialogOpen(true)
  }

  const handleDirectoryPicker = async () => {
    const picker = resolveDirectoryPicker(productShellBridge)
    if (!picker) {
      setWorkspaceDialogError("Your current environment can't open a folder picker.")
      return
    }

    try {
      const selection = await picker()
      setWorkspaceDialogError(null)
      setWorkspaceDraft(selection)
    } catch (error) {
      if (isDirectoryPickerCancelled(error)) {
        return
      }
      setForceManualWorkspaceEntry(true)
      setWorkspaceDialogError(
        error instanceof Error
          ? error.message
          : "Unable to automatically read the directory path; please manually enter the directory name and path."
      )
    }
  }

  const submitWorkspace = async () => {
    if (isManualWorkspaceEntry && (!workspaceDraft.name.trim() || !workspaceDraft.path.trim())) {
      setWorkspaceDialogError("Please enter both directory name and directory path.")
      return
    }

    if (!isManualWorkspaceEntry && (!workspaceDraft.name.trim() || !workspaceDraft.path.trim())) {
      setWorkspaceDialogError("Please choose a directory first.")
      return
    }

    setWorkspaceDialogSubmitting(true)
    setWorkspaceDialogError(null)
    try {
      await onAddProject(workspaceDraft.name.trim(), workspaceDraft.path.trim())
      setWorkspaceDraft({ name: "", path: "" })
      setWorkspaceDialogOpen(false)
    } catch (error) {
      setWorkspaceDialogError(
        error instanceof Error ? error.message : "Failed to Add directory"
      )
    } finally {
      setWorkspaceDialogSubmitting(false)
    }
  }

  return (
    <>
      <aside
        className="relative h-screen bg-[#f9f5f1] border-r border-[#e8e4e0] flex flex-col flex-shrink-0"
        style={{ width }}
      >
        <div className="h-1 bg-gradient-to-r from-[#e87b5f] via-[#f5a76c] to-[#8bc28f]" />

        <div className="p-2 space-y-0.5">
          <button
            onClick={() => void onNewSession()}
            className="w-full flex items-center gap-3 px-3 py-2 text-sm text-foreground/80 hover:bg-[#efe9e4] rounded-lg transition-colors font-medium"
          >
            <SquarePen className="w-4 h-4" />
            New session
          </button>
        </div>

        <div className="flex-1 px-2 mt-4 overflow-y-auto overflow-x-visible">
          <div className="flex items-center justify-between px-3 mb-2">
            <span className="text-xs font-medium text-muted-foreground">Sessions</span>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="p-1 hover:bg-[#efe9e4] rounded"
                  onClick={handleAddProjectClick}
                  aria-label="Add a new directory"
                >
                  <FolderPlus className="w-3.5 h-3.5 text-muted-foreground" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={6}>
                Add a new directory
              </TooltipContent>
            </Tooltip>
          </div>

          <div className="space-y-1">
            {projects.length === 0 && (
              <p className="px-3 py-4 text-xs text-muted-foreground text-center">
                No projects yet. Click <FolderPlus className="inline w-3 h-3 mx-0.5" /> to add one.
              </p>
            )}
            {projects.map((project) => (
              <ProjectGroup
                key={project.id}
                project={project}
                activeSessionId={activeSessionId}
                isActiveProject={activeProjectId === project.id}
                onNewSession={() => onNewSession(project.id)}
                onSessionChange={(sid) => onSessionChange(sid, project.id)}
                onRename={(name, pathLabel) => onRenameProject(project.id, name, pathLabel)}
                onRemove={() => onRemoveProject(project.id)}
              />
            ))}
          </div>
        </div>

        <div className="p-2 border-t border-[#e8e4e0]">
          <button
            onClick={onSettingsOpen}
            className="w-full flex items-center gap-3 px-3 py-2 text-sm text-foreground/80 hover:bg-[#efe9e4] rounded-lg transition-colors"
          >
            <Settings className="w-4 h-4" />
            Settings
          </button>
        </div>

        <Tooltip>
          <TooltipTrigger asChild>
            <div
              onMouseDown={onResizeStart}
              className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize group z-30"
            >
              <div className="h-full w-0.5 ml-auto opacity-0 group-hover:opacity-100 transition-opacity bg-[#e87b5f]/60 rounded-full" />
            </div>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={8}>
            Drag to resize
          </TooltipContent>
        </Tooltip>
      </aside>

      <Dialog open={workspaceDialogOpen} onOpenChange={setWorkspaceDialogOpen}>
        <DialogContent className="sm:max-w-md" showCloseButton={!workspaceDialogSubmitting}>
          <DialogHeader>
            <DialogTitle>Add directory</DialogTitle>
            <DialogDescription>
              Register a trusted local directory for sessions and runs.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {isManualWorkspaceEntry ? (
              <>
                <p className="text-xs text-muted-foreground">
                  {manualEntryHint}
                </p>
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="workspace-name-input">Directory name</label>
                  <input
                    id="workspace-name-input"
                    value={workspaceDraft.name}
                    onChange={(event) =>
                      setWorkspaceDraft((prev) => ({ ...prev, name: event.target.value }))
                    }
                    className="w-full rounded-lg border border-[#e8e4e0] bg-background px-3 py-2 text-sm"
                    placeholder="nutstore-bot"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="workspace-path-input">Directory path</label>
                  <input
                    id="workspace-path-input"
                    value={workspaceDraft.path}
                    onChange={(event) =>
                      setWorkspaceDraft((prev) => ({ ...prev, path: event.target.value }))
                    }
                    className="w-full rounded-lg border border-[#e8e4e0] bg-background px-3 py-2 text-sm"
                    placeholder="/path/to/workspace"
                  />
                </div>
              </>
            ) : (
              <>
                <Button type="button" variant="outline" onClick={() => void handleDirectoryPicker()}>
                  Select directory
                </Button>
                {workspaceDraft.path ? (
                  <div className="rounded-lg border border-[#e8e4e0] bg-background px-3 py-2 text-sm">
                    <p className="font-medium text-foreground">{workspaceDraft.name}</p>
                    <p className="text-xs text-muted-foreground break-all">{workspaceDraft.path}</p>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Choose a local directory to create a workspace.
                  </p>
                )}
              </>
            )}
            {workspaceDialogError && (
              <p className="text-sm text-destructive">{workspaceDialogError}</p>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={workspaceDialogSubmitting}
              onClick={() => setWorkspaceDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={
                workspaceDialogSubmitting ||
                (!isManualWorkspaceEntry &&
                  (!workspaceDraft.name.trim() || !workspaceDraft.path.trim()))
              }
              onClick={() => void submitWorkspace()}
            >
              {workspaceDialogSubmitting ? "Adding..." : "Add directory"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

interface ProjectGroupProps {
  project: Project
  isActiveProject: boolean
  activeSessionId: string | null
  onNewSession: () => void | Promise<void>
  onSessionChange: (sessionId: string) => void
  onRename: (name: string, pathLabel?: string) => void | Promise<void>
  onRemove: () => void | Promise<void>
}

function ProjectGroup({
  project,
  isActiveProject,
  activeSessionId,
  onNewSession,
  onSessionChange,
  onRename,
  onRemove,
}: ProjectGroupProps) {
  const [expanded, setExpanded] = useState(true)
  const [renameOpen, setRenameOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [projectName, setProjectName] = useState(project.name)
  const [pathLabel, setPathLabel] = useState(project.path)
  const [showAllSessions, setShowAllSessions] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    setProjectName(project.name)
    setPathLabel(project.path)
  }, [project.name, project.path])

  const visibleSessions = showAllSessions
    ? project.sessions
    : project.sessions.slice(0, SESSION_LIMIT)

  const submitRename = async () => {
    if (!projectName.trim()) {
      setActionError("Directory name is required.")
      return
    }
    setIsSubmitting(true)
    setActionError(null)
    try {
      await onRename(projectName.trim(), pathLabel.trim() || project.path)
      setRenameOpen(false)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to rename workspace")
    } finally {
      setIsSubmitting(false)
    }
  }

  const submitDelete = async () => {
    setIsSubmitting(true)
    setActionError(null)
    try {
      await onRemove()
      setDeleteOpen(false)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to remove workspace")
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <div className="space-y-0.5">
        <div
          className={cn(
            "group/project flex items-center gap-1 px-1 py-1.5 rounded-lg hover:bg-[#efe9e4] transition-colors",
            isActiveProject && "bg-[#efe9e4]/60"
          )}
        >
          <button
            onClick={() => setExpanded((value) => !value)}
            className="group/folder relative w-5 h-5 flex items-center justify-center flex-shrink-0 rounded transition-colors hover:bg-[#e0d9d3]"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            <Folder className="w-4 h-4 text-muted-foreground transition-opacity group-hover/folder:opacity-0 group-focus-visible/folder:opacity-0" />
            {expanded ? (
              <ChevronDown className="absolute w-3.5 h-3.5 text-foreground/70 opacity-0 transition-opacity group-hover/folder:opacity-100 group-focus-visible/folder:opacity-100" />
            ) : (
              <ChevronRight className="absolute w-3.5 h-3.5 text-foreground/70 opacity-0 transition-opacity group-hover/folder:opacity-100 group-focus-visible/folder:opacity-100" />
            )}
          </button>

          <Tooltip>
            <TooltipTrigger asChild>
              <button
                className="flex-1 text-sm text-foreground/80 text-left truncate"
                onClick={() => setExpanded((value) => !value)}
              >
                {project.name}
              </button>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={8}>
              {project.path}
            </TooltipContent>
          </Tooltip>

          <div className="flex items-center gap-0.5 flex-shrink-0 opacity-0 pointer-events-none transition-opacity group-hover/project:opacity-100 group-hover/project:pointer-events-auto group-focus-within/project:opacity-100 group-focus-within/project:pointer-events-auto">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => {
                      setActionError(null)
                      setRenameOpen(true)
                    }}
                    className="p-1 hover:bg-[#e0d9d3] rounded transition-colors"
                    aria-label={`Rename workspace ${project.name}`}
                  >
                    <PencilLine className="w-4 h-4 text-muted-foreground" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={6}>
                  Rename workspace
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => void onNewSession()}
                    className="p-1 hover:bg-[#e0d9d3] rounded transition-colors"
                    aria-label={`Start new session in ${project.name}`}
                  >
                    <Edit className="w-4 h-4 text-muted-foreground" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={6}>
                  Start new session
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => {
                      setActionError(null)
                      setDeleteOpen(true)
                    }}
                    className="p-1 hover:bg-[#e0d9d3] rounded transition-colors"
                    aria-label={`Remove workspace ${project.name}`}
                  >
                    <Trash2 className="w-4 h-4 text-muted-foreground" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={6}>
                  Remove workspace
                </TooltipContent>
              </Tooltip>
            </div>
        </div>

        {expanded && visibleSessions.map((session) => (
          <SessionItem
            key={session.id}
            session={session}
            isActive={activeSessionId === session.id}
            onClick={() => onSessionChange(session.id)}
          />
        ))}

        {expanded && project.sessions.length > SESSION_LIMIT && (
          <button
            onClick={() => setShowAllSessions((value) => !value)}
            className="w-full flex items-center gap-1.5 pl-7 pr-3 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-[#efe9e4] rounded-lg transition-colors"
          >
            {showAllSessions ? (
              <>
                <ChevronUp className="w-3 h-3" /> Show less
              </>
            ) : (
              <>
                <ChevronDown className="w-3 h-3" /> Show {project.sessions.length - SESSION_LIMIT} more
              </>
            )}
          </button>
        )}
      </div>

      <Dialog
        open={renameOpen}
        onOpenChange={(open) => {
          setRenameOpen(open)
          if (!open) {
            setActionError(null)
            setProjectName(project.name)
            setPathLabel(project.path)
          }
        }}
      >
        <DialogContent className="sm:max-w-md" showCloseButton={!isSubmitting}>
          <DialogHeader>
            <DialogTitle>Edit workspace</DialogTitle>
            <DialogDescription>
              Update the display name or path label shown in the sidebar.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor={`rename-workspace-name-${project.id}`}>Directory name</label>
              <input
                id={`rename-workspace-name-${project.id}`}
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                className="w-full rounded-lg border border-[#e8e4e0] bg-background px-3 py-2 text-sm"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor={`rename-workspace-path-${project.id}`}>Path label</label>
              <input
                id={`rename-workspace-path-${project.id}`}
                value={pathLabel}
                onChange={(event) => setPathLabel(event.target.value)}
                className="w-full rounded-lg border border-[#e8e4e0] bg-background px-3 py-2 text-sm"
              />
            </div>
            {actionError && <p className="text-sm text-destructive">{actionError}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={isSubmitting} onClick={() => setRenameOpen(false)}>
              Cancel
            </Button>
            <Button type="button" disabled={isSubmitting} onClick={() => void submitRename()}>
              {isSubmitting ? "Saving..." : "Save changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open)
          if (!open) {
            setActionError(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove workspace?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the workspace and all persisted sessions under it from the local sidecar database.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {actionError && <p className="text-sm text-destructive">{actionError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isSubmitting}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void submitDelete()} disabled={isSubmitting}>
              {isSubmitting ? "Removing..." : "Remove workspace"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

interface SessionItemProps {
  session: Session
  isActive: boolean
  onClick: () => void
}

function SessionItem({ session, isActive, onClick }: SessionItemProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-2 pl-7 pr-3 py-1.5 text-sm text-foreground/80 hover:bg-[#efe9e4] rounded-lg transition-colors",
        isActive && "bg-[#efe9e4]"
      )}
    >
      <span className="flex-1 text-left truncate">{session.title}</span>
      <span className="text-xs text-muted-foreground flex-shrink-0">
        {formatRelativeTime(session.lastMessageAt ?? session.updatedAt)}
      </span>
    </button>
  )
}

function formatRelativeTime(value: string): string {
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) {
    return ""
  }

  const diffMs = Date.now() - timestamp
  const diffMinutes = Math.max(0, Math.floor(diffMs / 60000))
  if (diffMinutes < 1) {
    return "just now"
  }
  if (diffMinutes < 60) {
    return `${diffMinutes}m`
  }

  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) {
    return `${diffHours}h`
  }

  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 7) {
    return `${diffDays}d`
  }

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(new Date(timestamp))
}

function basename(value: string): string {
  const normalized = value.trim().replace(/[\\/]+$/, "")
  if (normalized === "") {
    return ""
  }
  const parts = normalized.split(/[\\/]/)
  return parts[parts.length - 1] ?? ""
}

function resolveDirectoryPicker(
  bridge: ProductShellBridge | null
): (() => Promise<WorkspaceDraft>) | null {
  const shellPicker = bridge?.pickWorkspaceDirectory
  if (typeof shellPicker === "function") {
    return async () => {
      const selection = await shellPicker()
      return normalizeDirectorySelection(selection)
    }
  }

  const shellWindowPicker = bridge?.window?.showDirectoryPicker
  if (typeof shellWindowPicker === "function") {
    return async () => {
      const selection = await shellWindowPicker()
      return normalizeDirectorySelection(selection)
    }
  }

  if (typeof window === "undefined") {
    return null
  }

  const browserPicker = (window as Window & { showDirectoryPicker?: DirectoryPicker }).showDirectoryPicker
  if (typeof browserPicker === "function") {
    return async () => {
      const selection = await browserPicker()
      return normalizeDirectorySelection(selection)
    }
  }

  return null
}

function normalizeDirectorySelection(selection: DirectoryPickerSelection): WorkspaceDraft {
  const path =
    selection.pathLabel?.trim() ||
    selection.realPath?.trim() ||
    selection.path?.trim() ||
    selection.fullPath?.trim() ||
    ""

  if (!path) {
    throw new Error("Unable to automatically read the directory path; please manually enter the directory name and path.")
  }

  const name = selection.name?.trim() || basename(path) || "Workspace"
  return { name, path }
}

function isDirectoryPickerCancelled(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") {
    return true
  }

  if (error instanceof Error) {
    const normalized = error.message.toLowerCase()
    return normalized.includes("cancel") || normalized.includes("abort")
  }

  return false
}
