"use client"

import { useRef, useState } from "react"
import { SquarePen, Settings, Folder, FolderPlus, MoreVertical, Edit, ChevronRight, ChevronDown, ChevronUp } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import type { Project, Session } from "@/app/page"

interface SidebarProps {
  projects: Project[]
  activeProjectId: number | null
  activeSessionId: number | null
  width: number
  onAddProject: (name: string, path: string) => void
  onNewSession: (projectId?: number) => void
  onSessionChange: (sessionId: number, projectId: number) => void
  onRemoveProject: (projectId: number) => void
  onSettingsOpen?: () => void
  onResizeStart: (e: React.MouseEvent) => void
}

export function Sidebar({
  projects,
  activeProjectId,
  activeSessionId,
  width,
  onAddProject,
  onNewSession,
  onSessionChange,
  onRemoveProject,
  onSettingsOpen,
  onResizeStart,
}: SidebarProps) {
  const [hoveringAddBtn, setHoveringAddBtn] = useState(false)
  const folderInputRef = useRef<HTMLInputElement>(null)

  const handleAddProjectClick = () => {
    folderInputRef.current?.click()
  }

  const handleFolderSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return
    // The webkitRelativePath gives "folderName/file.ext", take the first segment
    const firstFile = files[0]
    const parts = firstFile.webkitRelativePath.split("/")
    const folderName = parts[0] || firstFile.name
    onAddProject(folderName, folderName)
    // Reset so the same folder can be re-selected
    e.target.value = ""
  }

  const handleNewSessionClick = () => {
    onNewSession()
  }

  return (
    <aside
      className="relative h-screen bg-[#f9f5f1] border-r border-[#e8e4e0] flex flex-col flex-shrink-0"
      style={{ width }}
    >
      {/* Top accent bar */}
      <div className="h-1 bg-gradient-to-r from-[#e87b5f] via-[#f5a76c] to-[#8bc28f]" />

      {/* Menu items */}
      <div className="p-2 space-y-0.5">
        <button
          onClick={handleNewSessionClick}
          className="w-full flex items-center gap-3 px-3 py-2 text-sm text-foreground/80 hover:bg-[#efe9e4] rounded-lg transition-colors font-medium"
        >
          <SquarePen className="w-4 h-4" />
          New session
        </button>
      </div>

      {/* Sessions section */}
      <div className="flex-1 px-2 mt-4 overflow-y-auto overflow-x-visible">
        {/* Section header */}
        <div className="flex items-center justify-between px-3 mb-2">
          <span className="text-xs font-medium text-muted-foreground">Sessions</span>
          <div className="relative">
            <button
              className="p-1 hover:bg-[#efe9e4] rounded"
              onClick={handleAddProjectClick}
              onMouseEnter={() => setHoveringAddBtn(true)}
              onMouseLeave={() => setHoveringAddBtn(false)}
              aria-label="Add a new project"
            >
              <FolderPlus className="w-3.5 h-3.5 text-muted-foreground" />
            </button>
            {hoveringAddBtn && (
              <div className="absolute right-0 bottom-full mb-1 px-2 py-1 bg-foreground text-background text-xs rounded whitespace-nowrap pointer-events-none z-50">
                Add a new project
              </div>
            )}
          </div>
        </div>

        {/* Hidden folder input */}
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error — webkitdirectory is non-standard but well-supported
          webkitdirectory=""
          multiple
          className="hidden"
          onChange={handleFolderSelected}
        />

        {/* Project list */}
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
              onRemove={() => onRemoveProject(project.id)}
            />
          ))}
        </div>
      </div>

      {/* Settings */}
      <div className="p-2 border-t border-[#e8e4e0]">
        <button
          onClick={onSettingsOpen}
          className="w-full flex items-center gap-3 px-3 py-2 text-sm text-foreground/80 hover:bg-[#efe9e4] rounded-lg transition-colors"
        >
          <Settings className="w-4 h-4" />
          Settings
        </button>
      </div>

      {/* ResizeHandle */}
      <div
        onMouseDown={onResizeStart}
        className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize group z-30"
        title="Drag to resize"
      >
        <div className="h-full w-0.5 ml-auto opacity-0 group-hover:opacity-100 transition-opacity bg-[#e87b5f]/60 rounded-full" />
      </div>
    </aside>
  )
}

// ─── ProjectGroup ─────────────────────────────────────────────────────────────

interface ProjectGroupProps {
  project: Project
  isActiveProject: boolean
  activeSessionId: number | null
  onNewSession: () => void
  onSessionChange: (sessionId: number) => void
  onRemove: () => void
}

const SESSION_LIMIT = 5

function ProjectGroup({
  project,
  isActiveProject,
  activeSessionId,
  onNewSession,
  onSessionChange,
  onRemove,
}: ProjectGroupProps) {
  const [expanded, setExpanded] = useState(true)
  const [hovering, setHovering] = useState(false)
  const [hoveringFolder, setHoveringFolder] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [hoveringEditBtn, setHoveringEditBtn] = useState(false)
  const [editingName, setEditingName] = useState(false)
  const [projectName, setProjectName] = useState(project.name)
  const [showAllSessions, setShowAllSessions] = useState(false)

  const visibleSessions = showAllSessions
    ? project.sessions
    : project.sessions.slice(0, SESSION_LIMIT)

  return (
    <div className="space-y-0.5">
      {/* Project row */}
      <div
        className={cn(
          "flex items-center gap-1 px-1 py-1.5 rounded-lg hover:bg-[#efe9e4] transition-colors group",
          isActiveProject && "bg-[#efe9e4]/60"
        )}
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => {
          if (!dropdownOpen) setHovering(false)
        }}
      >
        {/* Expand button */}
        <button
          onClick={() => setExpanded((v) => !v)}
          onMouseEnter={() => setHoveringFolder(true)}
          onMouseLeave={() => setHoveringFolder(false)}
          className="w-5 h-5 flex items-center justify-center flex-shrink-0 rounded transition-colors hover:bg-[#e0d9d3]"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {hoveringFolder ? (
            expanded ? (
              <ChevronDown className="w-3.5 h-3.5 text-foreground/70" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5 text-foreground/70" />
            )
          ) : (
            <Folder className="w-4 h-4 text-muted-foreground" />
          )}
        </button>

        {/* Project name */}
        {editingName ? (
          <input
            autoFocus
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            onBlur={() => setEditingName(false)}
            onKeyDown={(e) => e.key === "Enter" && setEditingName(false)}
            className="flex-1 text-sm bg-transparent border-b border-foreground/30 focus:outline-none px-1"
          />
        ) : (
          <button
            className="flex-1 text-sm text-foreground/80 text-left truncate"
            onClick={() => setExpanded((v) => !v)}
          >
            {projectName}
          </button>
        )}

        {/* Hover actions */}
        {(hovering || dropdownOpen) && !editingName && (
          <div className="flex items-center gap-0.5 flex-shrink-0">
            {/* More options — uses Radix UI Portal to escape sidebar overflow */}
            <DropdownMenu
              open={dropdownOpen}
              onOpenChange={(open) => {
                setDropdownOpen(open)
                if (!open) setHovering(false)
              }}
            >
              <DropdownMenuTrigger asChild>
                <button
                  className="p-1 hover:bg-[#e0d9d3] rounded transition-colors"
                  aria-label="More options"
                >
                  <MoreVertical className="w-4 h-4 text-muted-foreground" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="min-w-[130px]">
                <DropdownMenuItem
                  onSelect={() => { setEditingName(true); setDropdownOpen(false) }}
                >
                  Edit name
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => { onRemove(); setDropdownOpen(false) }}
                >
                  Remove
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* Start new session */}
            <div className="relative">
              <button
                onClick={onNewSession}
                onMouseEnter={() => setHoveringEditBtn(true)}
                onMouseLeave={() => setHoveringEditBtn(false)}
                className="p-1 hover:bg-[#e0d9d3] rounded transition-colors"
                aria-label="Start new session"
              >
                <Edit className="w-4 h-4 text-muted-foreground" />
              </button>
              {hoveringEditBtn && (
                <div className="absolute right-0 bottom-full mb-1 px-2 py-1 bg-foreground text-background text-xs rounded whitespace-nowrap pointer-events-none z-10">
                  New session in {projectName}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Session list */}
      {expanded && visibleSessions.map((session) => (
        <SessionItem
          key={session.id}
          session={session}
          isActive={activeSessionId === session.id}
          onClick={() => onSessionChange(session.id)}
        />
      ))}

      {/* Toggle more sessions */}
      {expanded && project.sessions.length > SESSION_LIMIT && (
        <button
          onClick={() => setShowAllSessions((v) => !v)}
          className="w-full flex items-center gap-1.5 pl-7 pr-3 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-[#efe9e4] rounded-lg transition-colors"
        >
          {showAllSessions ? (
            <><ChevronUp className="w-3 h-3" /> Show less</>
          ) : (
            <><ChevronDown className="w-3 h-3" /> Show {project.sessions.length - SESSION_LIMIT} more</>
          )}
        </button>
      )}
    </div>
  )
}

// ─── SessionItem ──────────────────────────────────────────────────────────────

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
      <span className="flex-1 text-left truncate">{session.label}</span>
      <span className="text-xs text-muted-foreground flex-shrink-0">{session.time}</span>
    </button>
  )
}
