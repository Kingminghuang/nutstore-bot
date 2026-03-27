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
      hasMoreHistory: false,
      nextBeforeSequence: null,
      isLoadingHistory: false,
    },
  ],
}

describe("Sidebar workspace controls", () => {
  afterEach(() => {
    delete (window as Window & { __NSBOT_SHELL__?: unknown }).__NSBOT_SHELL__
    delete (window as Window & { showDirectoryPicker?: unknown }).showDirectoryPicker
  })

  it("opens Add directory dialog and submits manual values", async () => {
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

    fireEvent.click(screen.getByLabelText("Add a new directory"))
    fireEvent.change(screen.getByLabelText("Directory name"), {
      target: { value: "repo-a" },
    })
    fireEvent.change(screen.getByLabelText("Directory path"), {
      target: { value: "/tmp/repo-a" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add directory" }))

    await waitFor(() => {
      expect(onAddProject).toHaveBeenCalledWith("repo-a", "/tmp/repo-a")
    })
  })

  it("shows the add project tooltip on hover", async () => {
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

    fireEvent.focus(screen.getByLabelText("Add a new directory"))

    await waitFor(() => {
      expect(screen.getByRole("tooltip")).toHaveTextContent("Add a new directory")
    })
  })

  it("uses directory picker mode when available and submits picked values", async () => {
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

    fireEvent.click(screen.getByLabelText("Add a new directory"))
    expect(screen.queryByLabelText("Directory name")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Directory path")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Select directory" }))

    await waitFor(() => {
      expect(screen.getByText("repo-from-shell")).toBeInTheDocument()
      expect(screen.getByText("/Users/demo/repo-from-shell")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "Add directory" }))
    await waitFor(() => {
      expect(onAddProject).toHaveBeenCalledWith("repo-from-shell", "/Users/demo/repo-from-shell")
    })
  })

  it("falls back to __NSBOT_SHELL__.window.showDirectoryPicker when direct shell picker is unavailable", async () => {
    const onAddProject = vi.fn(async () => undefined)
    const shellWindowPicker = vi.fn(async () => ({
      name: "repo-from-shell-window",
      realPath: "/Users/demo/repo-from-shell-window",
      pathLabel: "/Users/demo/repo-from-shell-window",
    }))
    const browserPicker = vi.fn(async () => ({
      name: "repo-from-browser-window",
      realPath: "/Users/demo/repo-from-browser-window",
      pathLabel: "/Users/demo/repo-from-browser-window",
    }))

    ;(window as Window & { __NSBOT_SHELL__?: unknown }).__NSBOT_SHELL__ = {
      window: {
        showDirectoryPicker: shellWindowPicker,
      },
    }
    ;(window as Window & { showDirectoryPicker?: unknown }).showDirectoryPicker = browserPicker

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

    fireEvent.click(screen.getByLabelText("Add a new directory"))
    fireEvent.click(screen.getByRole("button", { name: "Select directory" }))
    await waitFor(() => {
      expect(onAddProject).not.toHaveBeenCalled()
      expect(screen.getByText("/Users/demo/repo-from-shell-window")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole("button", { name: "Add directory" }))
    await waitFor(() => {
      expect(onAddProject).toHaveBeenCalledWith(
        "repo-from-shell-window",
        "/Users/demo/repo-from-shell-window"
      )
    })
    expect(shellWindowPicker).toHaveBeenCalledTimes(1)
    expect(browserPicker).not.toHaveBeenCalled()
  })

  it("keeps add directory disabled when picker is available but no folder is chosen", async () => {
    ;(window as Window & {
      __NSBOT_SHELL__?: {
        pickWorkspaceDirectory: () => Promise<{
          name: string
          realPath: string
          pathLabel: string
        }>
      }
    }).__NSBOT_SHELL__ = {
      pickWorkspaceDirectory: vi.fn(async () => {
        throw new Error("cancelled")
      }),
    }

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

    fireEvent.click(screen.getByLabelText("Add a new directory"))
    expect(screen.getByRole("button", { name: "Add directory" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Select directory" }))
    expect(screen.getByRole("button", { name: "Add directory" })).toBeDisabled()
  })

  it("shows manual fallback fields with user-friendly guidance when picker is unavailable", async () => {
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

    fireEvent.click(screen.getByLabelText("Add a new directory"))
    expect(screen.getByLabelText("Directory name")).toBeInTheDocument()
    expect(screen.getByLabelText("Directory path")).toBeInTheDocument()

    await waitFor(() => {
      expect(
        screen.getByText(
          "This environment doesn't support direct folder selection. Please enter the directory name and path manually."
        )
      ).toBeInTheDocument()
    })
    expect(screen.queryByRole("button", { name: "Use product shell" })).not.toBeInTheDocument()
  })

  it("falls back to manual inputs when browser showDirectoryPicker cannot provide a usable path", async () => {
    ;(window as Window & { showDirectoryPicker?: unknown }).showDirectoryPicker = vi.fn(
      async () => ({
        name: "browser-picked-folder",
      })
    )

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

    fireEvent.click(screen.getByLabelText("Add a new directory"))
    expect(screen.queryByLabelText("Directory name")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Directory path")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Select directory" }))

    await waitFor(() => {
      expect(
        screen.getByText("Unable to automatically read the directory path; please manually enter the directory name and path.")
      ).toBeInTheDocument()
    })
    expect(screen.getByLabelText("Directory name")).toBeInTheDocument()
    expect(screen.getByLabelText("Directory path")).toBeInTheDocument()
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

    fireEvent.mouseEnter(screen.getByText("nutstore-bot"))
    fireEvent.click(screen.getByLabelText("Rename workspace nutstore-bot"))
    fireEvent.change(screen.getByLabelText("Directory name"), {
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

    fireEvent.mouseEnter(screen.getByText("nutstore-bot"))
    fireEvent.click(screen.getByLabelText("Remove workspace nutstore-bot"))
    fireEvent.click(screen.getByRole("button", { name: "Remove workspace" }))

    await waitFor(() => {
      expect(onRemoveProject).toHaveBeenCalledWith("ws_1")
    })
  })

  it("confirms session removal before deleting", async () => {
    const onRemoveSession = vi.fn(async () => undefined)

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
        onRemoveSession={onRemoveSession}
        onRemoveProject={vi.fn()}
        onResizeStart={vi.fn()}
      />
    )

    fireEvent.mouseEnter(screen.getByText("Backend driven title"))
    fireEvent.click(screen.getByLabelText("Remove session Backend driven title"))
    fireEvent.click(screen.getByRole("button", { name: "Remove session" }))

    await waitFor(() => {
      expect(onRemoveSession).toHaveBeenCalledWith("sess_1", "ws_1")
    })
  })
})
