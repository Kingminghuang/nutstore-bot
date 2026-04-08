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

import type { Project, Session } from "@/features/session"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/shared/ui/alert-dialog"
import { Button } from "@/shared/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/shared/ui/dialog"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/shared/ui/tooltip"
import {
  canUseTauriDirectoryPicker,
  pickDirectoryWithTauriDialog,
} from "@/shared/lib/tauri-directory-picker"
import { cn } from "@/shared/lib/utils"

interface SidebarProps {
  projects: Project[]
  activeProjectId: string | null
  activeSessionId: string | null
  width: number
  onAddProject: (name: string, path: string) => void | Promise<void>
  onRenameProject: (projectId: string, name: string, pathLabel?: string) => void | Promise<void>
  onNewSession: (projectId?: string) => void | Promise<void>
  onSessionChange: (sessionId: string, projectId: string) => void
  onRemoveSession?: (sessionId: string, projectId: string) => void | Promise<void>
  onRemoveProject: (projectId: string) => void | Promise<void>
  onSettingsOpen?: () => void
  onResizeStart: (e: React.MouseEvent) => void
}

type WorkspaceDraft = {
  name: string
  path: string
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
  onRemoveSession,
  onRemoveProject,
  onSettingsOpen,
  onResizeStart,
}: SidebarProps) {
  const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false)
  const [workspaceDialogError, setWorkspaceDialogError] = useState<string | null>(null)
  const [workspaceDialogSubmitting, setWorkspaceDialogSubmitting] = useState(false)
  const [forceManualWorkspaceEntry, setForceManualWorkspaceEntry] = useState(false)
  const [workspaceDraft, setWorkspaceDraft] = useState<WorkspaceDraft>({ name: "", path: "" })

  const hasNativeDirectoryPicker = canUseTauriDirectoryPicker()
  const canUseDirectoryPicker = hasNativeDirectoryPicker && !forceManualWorkspaceEntry
  const isManualWorkspaceEntry = !canUseDirectoryPicker
  const manualEntryHint =
    !hasNativeDirectoryPicker
      ? "This environment doesn't support direct folder selection. Please enter the directory name and path manually."
      : "Please enter the directory name and path manually."

  const handleAddProjectClick = () => {
    setForceManualWorkspaceEntry(false)
    setWorkspaceDraft({ name: "", path: "" })
    setWorkspaceDialogError(null)
    setWorkspaceDialogOpen(true)
  }

  const handleDirectoryPicker = async () => {
    if (!hasNativeDirectoryPicker) {
      setWorkspaceDialogError("Your current environment can't open a folder picker.")
      return
    }

    const result = await pickDirectoryWithTauriDialog()
    if (result.status === "selected") {
      setWorkspaceDialogError(null)
      setWorkspaceDraft(result.selection)
      return
    }

    if (result.status === "cancelled") {
      return
    }

    setForceManualWorkspaceEntry(true)
    setWorkspaceDialogError(
      result.status === "error"
        ? result.message
        : "Unable to open the native folder picker; please manually enter the directory name and path."
    )
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
                onRemoveSession={(sid) => onRemoveSession?.(sid, project.id)}
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
  onRemoveSession?: (sessionId: string) => void | Promise<void>
  onRename: (name: string, pathLabel?: string) => void | Promise<void>
  onRemove: () => void | Promise<void>
}

function ProjectGroup({
  project,
  isActiveProject,
  activeSessionId,
  onNewSession,
  onSessionChange,
  onRemoveSession,
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
            onRemove={onRemoveSession ? () => onRemoveSession(session.id) : undefined}
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
  onRemove?: () => void | Promise<void>
}

function SessionItem({ session, isActive, onClick, onRemove }: SessionItemProps) {
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const submitDelete = async () => {
    if (!onRemove) {
      return
    }
    setIsSubmitting(true)
    setActionError(null)
    try {
      await onRemove()
      setDeleteOpen(false)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Failed to remove session")
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <div
        className={cn(
          "group/session w-full flex items-center gap-2 pl-7 pr-2 py-1.5 text-sm text-foreground/80 hover:bg-[#efe9e4] rounded-lg transition-colors",
          isActive && "bg-[#efe9e4]"
        )}
      >
        <button onClick={onClick} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <span className="flex-1 text-left truncate">{session.title}</span>
          <span className="text-xs text-muted-foreground flex-shrink-0">
            {formatRelativeTime(session.lastMessageAt ?? session.updatedAt)}
          </span>
        </button>
        {onRemove ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => {
                  setActionError(null)
                  setDeleteOpen(true)
                }}
                className="p-1 hover:bg-[#e0d9d3] rounded transition-colors opacity-0 pointer-events-none group-hover/session:opacity-100 group-hover/session:pointer-events-auto group-focus-within/session:opacity-100 group-focus-within/session:pointer-events-auto"
                aria-label={`Remove session ${session.title}`}
              >
                <Trash2 className="w-3.5 h-3.5 text-muted-foreground" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" sideOffset={6}>
              Remove session
            </TooltipContent>
          </Tooltip>
        ) : null}
      </div>

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
            <AlertDialogTitle>Remove session?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the session and all persisted messages, runs, and attachments from the local sidecar database.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {actionError && <p className="text-sm text-destructive">{actionError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isSubmitting}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void submitDelete()} disabled={isSubmitting}>
              {isSubmitting ? "Removing..." : "Remove session"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
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
