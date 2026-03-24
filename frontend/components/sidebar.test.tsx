import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import { Sidebar } from "@/components/sidebar"

const project = {
  id: "ws_1",
  name: "nutstore-bot",
  path: "/tmp/nutstore-bot",
  sessions: [
    {
      id: "sess_1",
      workspaceId: "ws_1",
      title: "Backend driven title",
      titleSource: "model",
      createdAt: "2026-03-24T12:00:00Z",
      updatedAt: new Date().toISOString(),
      lastMessageAt: new Date().toISOString(),
      messageCount: 1,
      lastMessagePreview: "hello",
      activeConnectionId: "prov_openai",
      activeModelId: "gpt-5.4",
      messages: [],
    },
  ],
}

describe("Sidebar workspace controls", () => {
  afterEach(() => {
    delete (window as Window & { __NSBOT_SHELL__?: unknown }).__NSBOT_SHELL__
  })

  it("opens add workspace dialog and submits manual values", async () => {
    const onAddProject = vi.fn(async () => undefined)

    render(
      <Sidebar
        projects={[]}
        activeProjectId={null}
        activeSessionId={null}
        width={230}
        onAddProject={onAddProject}
        onRenameProject={vi.fn()}
        onNewSession={vi.fn()}
        onSessionChange={vi.fn()}
        onRemoveProject={vi.fn()}
        onResizeStart={vi.fn()}
      />
    )

    fireEvent.click(screen.getByLabelText("Add a new project"))
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "repo-a" },
    })
    fireEvent.change(screen.getByLabelText("Workspace path"), {
      target: { value: "/tmp/repo-a" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add workspace" }))

    await waitFor(() => {
      expect(onAddProject).toHaveBeenCalledWith("repo-a", "/tmp/repo-a")
    })
  })

  it("uses the product shell workspace picker when available", async () => {
    const onAddProject = vi.fn(async () => undefined)
    ;(window as Window & {
      __NSBOT_SHELL__?: {
        pickWorkspaceDirectory: () => Promise<{
          name: string
          realPath: string
          pathLabel: string
        }>
      }
    }).__NSBOT_SHELL__ = {
      pickWorkspaceDirectory: vi.fn(async () => ({
        name: "repo-from-shell",
        realPath: "/Users/demo/repo-from-shell",
        pathLabel: "/Users/demo/repo-from-shell",
      })),
    }

    render(
      <Sidebar
        projects={[]}
        activeProjectId={null}
        activeSessionId={null}
        width={230}
        onAddProject={onAddProject}
        onRenameProject={vi.fn()}
        onNewSession={vi.fn()}
        onSessionChange={vi.fn()}
        onRemoveProject={vi.fn()}
        onResizeStart={vi.fn()}
      />
    )

    fireEvent.click(screen.getByLabelText("Add a new project"))
    fireEvent.click(screen.getByRole("button", { name: "Pick folder" }))

    await waitFor(() => {
      expect(screen.getByLabelText("Workspace name")).toHaveValue("repo-from-shell")
      expect(screen.getByLabelText("Workspace path")).toHaveValue("/Users/demo/repo-from-shell")
    })
  })

  it("explains the manual fallback when no product shell picker exists", async () => {
    render(
      <Sidebar
        projects={[]}
        activeProjectId={null}
        activeSessionId={null}
        width={230}
        onAddProject={vi.fn()}
        onRenameProject={vi.fn()}
        onNewSession={vi.fn()}
        onSessionChange={vi.fn()}
        onRemoveProject={vi.fn()}
        onResizeStart={vi.fn()}
      />
    )

    fireEvent.click(screen.getByLabelText("Add a new project"))
    fireEvent.click(screen.getByRole("button", { name: "Use product shell" }))

    await waitFor(() => {
      expect(
        screen.getByText(
          "Folder picking is available in the product shell. Paste a trusted local path here while running in the browser."
        )
      ).toBeInTheDocument()
    })
  })

  it("opens rename dialog and submits backend rename", async () => {
    const onRenameProject = vi.fn(async () => undefined)

    render(
      <Sidebar
        projects={[project]}
        activeProjectId="ws_1"
        activeSessionId="sess_1"
        width={230}
        onAddProject={vi.fn()}
        onRenameProject={onRenameProject}
        onNewSession={vi.fn()}
        onSessionChange={vi.fn()}
        onRemoveProject={vi.fn()}
        onResizeStart={vi.fn()}
      />
    )

    fireEvent.mouseEnter(screen.getByTitle("/tmp/nutstore-bot"))
    fireEvent.click(screen.getByLabelText("Rename workspace nutstore-bot"))
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "renamed-workspace" },
    })
    fireEvent.change(screen.getByLabelText("Path label"), {
      target: { value: "/tmp/renamed" },
    })
    fireEvent.click(screen.getByText("Save changes"))

    await waitFor(() => {
      expect(onRenameProject).toHaveBeenCalledWith("ws_1", "renamed-workspace", "/tmp/renamed")
    })
  })

  it("confirms workspace removal before deleting", async () => {
    const onRemoveProject = vi.fn(async () => undefined)

    render(
      <Sidebar
        projects={[project]}
        activeProjectId="ws_1"
        activeSessionId="sess_1"
        width={230}
        onAddProject={vi.fn()}
        onRenameProject={vi.fn()}
        onNewSession={vi.fn()}
        onSessionChange={vi.fn()}
        onRemoveProject={onRemoveProject}
        onResizeStart={vi.fn()}
      />
    )

    fireEvent.mouseEnter(screen.getByTitle("/tmp/nutstore-bot"))
    fireEvent.click(screen.getByLabelText("Remove workspace nutstore-bot"))
    fireEvent.click(screen.getByRole("button", { name: "Remove workspace" }))

    await waitFor(() => {
      expect(onRemoveProject).toHaveBeenCalledWith("ws_1")
    })
  })
})
