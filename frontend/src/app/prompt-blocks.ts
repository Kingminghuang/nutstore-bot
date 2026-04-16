import type { ComposerAttachment, DraftAttachment } from "@/features/session"
import type { WorkspaceEntrySearchResult } from "@/shared/api/sidecar"

export type AcpPromptBlock =
  | {
      type: "text"
      text: string
    }
  | {
      type: "resource_link"
      uri: string
      name?: string
      mimeType?: string
      title?: string
    }
  | {
      type: "resource"
      resource: {
        uri: string
        mimeType: string
        title?: string
      }
    }

export type UserDisplayBlock =
  | {
      type: "text"
      text: string
    }
  | {
      type: "resource"
      label: string
      uri: string
    }

export type BuiltPromptPayload = {
  promptBlocks: AcpPromptBlock[]
  displayBlocks: UserDisplayBlock[]
  displayText: string
  editableText: string
}

export type ComposerMention = WorkspaceEntrySearchResult
export const INLINE_MENTION_SENTINEL = "\uFFFC"

export type ComposerInlineBlock =
  | {
      type: "text"
      text: string
    }
  | {
      type: "mention"
      mention: ComposerMention
    }

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

function buildDisplayPayloadFromPromptBlocks(
  promptBlocks: AcpPromptBlock[],
  editableText: string
): BuiltPromptPayload {
  const displayBlocks: UserDisplayBlock[] = []

  for (const block of promptBlocks) {
    if (block.type === "text") {
      if (block.text.trim()) {
        displayBlocks.push({ type: "text", text: block.text })
      }
      continue
    }

    if (block.type === "resource") {
      displayBlocks.push({
        type: "resource",
        label: block.resource.title ?? block.resource.uri.split("/").filter(Boolean).pop() ?? "resource",
        uri: block.resource.uri,
      })
      continue
    }

    displayBlocks.push({
      type: "resource",
      label: block.title ?? block.name ?? block.uri.split("/").filter(Boolean).pop() ?? "resource",
      uri: block.uri,
    })
  }

  return {
    promptBlocks,
    displayBlocks,
    displayText: displayBlocks
      .map((block) => (block.type === "text" ? block.text : block.label))
      .join("\n")
      .trim(),
    editableText,
  }
}

export function rebuildPromptPayloadForEdit(
  nextText: string,
  existingPromptBlocks?: Array<Record<string, unknown>>
): BuiltPromptPayload {
  return rebuildPromptPayloadForEditFromComposerBlocks(
    [{ type: "text", text: nextText }],
    existingPromptBlocks
  )
}

export function rebuildPromptPayloadForEditFromComposerBlocks(
  composerBlocks: ComposerInlineBlock[],
  existingPromptBlocks?: Array<Record<string, unknown>>
): BuiltPromptPayload {
  const preservedBlocks: AcpPromptBlock[] = []

  if (Array.isArray(existingPromptBlocks)) {
    for (const block of existingPromptBlocks) {
      if (!block || typeof block !== "object") {
        continue
      }
      const blockType = asString(block.type)
      if (blockType === "resource") {
        const resource = block.resource
        if (!resource || typeof resource !== "object") {
          continue
        }
        const uri = asString((resource as Record<string, unknown>).uri)
        const mimeType = asString((resource as Record<string, unknown>).mimeType)
        if (!uri || !mimeType) {
          continue
        }
        preservedBlocks.push({
          type: "resource",
          resource: {
            uri,
            mimeType,
            title: asString((resource as Record<string, unknown>).title) ?? undefined,
          },
        })
        continue
      }
    }
  }

  const rebuilt = buildPromptPayloadFromComposerBlocks(composerBlocks, [])
  return buildDisplayPayloadFromPromptBlocks(
    [...rebuilt.promptBlocks, ...preservedBlocks],
    rebuilt.editableText
  )
}

function isSessionAttachment(
  attachment: ComposerAttachment | DraftAttachment
): attachment is ComposerAttachment {
  return "sessionId" in attachment
}

function buildAttachmentResourceUri(attachment: ComposerAttachment | DraftAttachment): string {
  return isSessionAttachment(attachment)
    ? `attachment://session/${attachment.id}`
    : `attachment://draft/${attachment.id}`
}

export function buildPromptBlocks(
  text: string,
  attachments: Array<ComposerAttachment | DraftAttachment>,
  mentions: ComposerMention[] = []
): AcpPromptBlock[] {
  return buildPromptPayload(text, attachments, mentions).promptBlocks
}

export function buildComposerInlineBlocks(
  value: string,
  mentions: ComposerMention[]
): ComposerInlineBlock[] {
  const blocks: ComposerInlineBlock[] = []
  let currentText = ""
  let mentionIndex = 0

  for (const char of value) {
    if (char !== INLINE_MENTION_SENTINEL) {
      currentText += char
      continue
    }

    if (currentText !== "") {
      blocks.push({ type: "text", text: currentText })
      currentText = ""
    }

    const mention = mentions[mentionIndex]
    mentionIndex += 1
    if (mention) {
      blocks.push({ type: "mention", mention })
    }
  }

  if (currentText !== "") {
    blocks.push({ type: "text", text: currentText })
  }

  return blocks
}

export function buildComposerBlocksFromPromptBlocks(
  promptBlocks?: Array<Record<string, unknown>>
): ComposerInlineBlock[] {
  if (!Array.isArray(promptBlocks)) {
    return [{ type: "text", text: "" }]
  }

  const blocks: ComposerInlineBlock[] = []
  for (const promptBlock of promptBlocks) {
    if (!promptBlock || typeof promptBlock !== "object") {
      continue
    }
    const blockType = asString(promptBlock.type)
    if (blockType === "text") {
      blocks.push({ type: "text", text: String(promptBlock.text ?? "") })
      continue
    }
    if (blockType !== "resource_link") {
      continue
    }
    const uri = asString(promptBlock.uri)
    if (!uri) {
      continue
    }
    blocks.push({
      type: "mention",
      mention: {
        name: asString(promptBlock.name) ?? asString(promptBlock.title) ?? uri.split("/").filter(Boolean).pop() ?? "resource",
        relativePath: asString(promptBlock.name) ?? asString(promptBlock.title) ?? uri,
        parentPath: "",
        absolutePath: uri.startsWith("file://") ? decodeURIComponent(uri.replace("file://", "")) : uri,
        uri,
        entryType: asString(promptBlock.mimeType) === "inode/directory" ? "directory" : "file",
      },
    })
  }

  if (blocks.length === 0) {
    return [{ type: "text", text: "" }]
  }

  if (blocks[blocks.length - 1]?.type !== "text") {
    blocks.push({ type: "text", text: "" })
  }

  return blocks
}

export function buildPromptPayloadFromInlineComposer(
  value: string,
  attachments: Array<ComposerAttachment | DraftAttachment>,
  mentions: ComposerMention[] = []
): BuiltPromptPayload {
  return buildPromptPayloadFromComposerBlocks(
    buildComposerInlineBlocks(value, mentions),
    attachments
  )
}

export function buildPromptPayloadFromComposerBlocks(
  composerBlocks: ComposerInlineBlock[],
  attachments: Array<ComposerAttachment | DraftAttachment>
): BuiltPromptPayload {
  const blocks: AcpPromptBlock[] = []

  for (const block of composerBlocks) {
    if (block.type === "text") {
      if (block.text.trim()) {
        blocks.push({ type: "text", text: block.text })
      }
      continue
    }

    blocks.push({
      type: "resource_link",
      uri: block.mention.uri,
      name: block.mention.name,
      mimeType: block.mention.entryType === "directory" ? "inode/directory" : undefined,
    })
  }

  for (const attachment of attachments) {
    const uri = buildAttachmentResourceUri(attachment)
    blocks.push({
      type: "resource",
      resource: {
        uri,
        mimeType: attachment.mimeType || "application/octet-stream",
        title: attachment.fileName,
      },
    })
  }

  const editableText = composerBlocks
    .filter((block): block is Extract<ComposerInlineBlock, { type: "text" }> => block.type === "text")
    .map((block) => block.text)
    .join("")
    .trim()

  return buildDisplayPayloadFromPromptBlocks(blocks, editableText)
}

export function buildPromptPayload(
  text: string,
  attachments: Array<ComposerAttachment | DraftAttachment>,
  mentions: ComposerMention[] = []
): BuiltPromptPayload {
  const serialized = mentions.length === 0 ? text : `${text}${mentions.map(() => INLINE_MENTION_SENTINEL).join("")}`
  return buildPromptPayloadFromInlineComposer(serialized, attachments, mentions)
}