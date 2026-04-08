"use client"

export type DirectorySelection = {
  name: string
  path: string
}

export type TauriDirectoryPickerResult =
  | { status: "selected"; selection: DirectorySelection }
  | { status: "cancelled" }
  | { status: "unavailable" }
  | { status: "error"; message: string }

type TauriWindow = Window & {
  __TAURI__?: unknown
  __TAURI_INTERNALS__?: unknown
}

export function canUseTauriDirectoryPicker(): boolean {
  if (typeof window === "undefined") {
    return false
  }

  const tauriWindow = window as TauriWindow
  return Boolean(tauriWindow.__TAURI__ || tauriWindow.__TAURI_INTERNALS__)
}

export async function pickDirectoryWithTauriDialog(): Promise<TauriDirectoryPickerResult> {
  if (!canUseTauriDirectoryPicker()) {
    return { status: "unavailable" }
  }

  try {
    const { open } = await import("@tauri-apps/plugin-dialog")
    const selection = await open({
      directory: true,
      multiple: false,
      title: "Select directory",
    })

    if (selection == null) {
      return { status: "cancelled" }
    }

    const path = normalizeSelectionPath(selection)
    if (!path) {
      return {
        status: "error",
        message:
          "Unable to automatically read the directory path; please manually enter the directory name and path.",
      }
    }

    return {
      status: "selected",
      selection: {
        name: basename(path) || "Workspace",
        path,
      },
    }
  } catch (error) {
    if (isCancelledError(error)) {
      return { status: "cancelled" }
    }

    return { status: "unavailable" }
  }
}

function normalizeSelectionPath(selection: unknown): string {
  if (typeof selection === "string") {
    return selection.trim()
  }

  if (Array.isArray(selection)) {
    const first = selection[0]
    return typeof first === "string" ? first.trim() : ""
  }

  return ""
}

function basename(value: string): string {
  const normalized = value.trim().replace(/[\\/]+$/, "")
  if (normalized === "") {
    return ""
  }
  const parts = normalized.split(/[\\/]/)
  return parts[parts.length - 1] ?? ""
}

function isCancelledError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") {
    return true
  }

  if (error instanceof Error) {
    const normalized = error.message.toLowerCase()
    return normalized.includes("cancel") || normalized.includes("abort")
  }

  return false
}
