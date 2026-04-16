import { afterEach, describe, expect, it, vi } from "vitest"

const requestMock = vi.fn()

vi.mock("@/shared/api/sidecar/acp-client", () => ({
  acpClient: {
    request: requestMock,
  },
}))

describe("sidecar-client ACP resource requests", () => {
  afterEach(() => {
    requestMock.mockReset()
    vi.resetModules()
  })

  it("loads provider catalog via ACP _nsbot/provider/catalog", async () => {
    requestMock.mockResolvedValueOnce({ providers: [] })

    const { getProviderCatalog } = await import("@/shared/api/sidecar")
    const response = await getProviderCatalog()

    expect(response).toEqual({ providers: [] })
    expect(requestMock).toHaveBeenCalledWith("_nsbot/provider/catalog")
  })

  it("requests timeline through ACP _nsbot/timeline/list", async () => {
    requestMock.mockResolvedValueOnce({
      events: [
        {
          eventId: "acpevt_1",
          sequenceNo: 1,
          createdAt: "2026-04-14T10:00:00Z",
          payload: {
            params: {
              update: {
                sessionUpdate: "user_message_chunk",
                content: {
                  type: "text",
                  text: "hello",
                },
              },
            },
          },
        },
      ],
      pagination: { hasMore: false, nextBeforeSequence: null },
    })

    const { getSessionTimeline } = await import("@/shared/api/sidecar")
    const response = await getSessionTimeline("sess_1", { limit: 20, beforeSequence: 100 })

    expect(response.events).toHaveLength(1)
    expect(response.events[0]).toMatchObject({
      eventId: "acpevt_1",
      sequenceNo: 1,
    })
    expect(requestMock).toHaveBeenCalledWith("_nsbot/timeline/list", {
      sessionId: "sess_1",
      limit: 20,
      beforeSequence: 100,
    })
  })

  it("projects user display text and resource labels from prompt blocks", async () => {
    const { projectConversationEvents } = await import("@/shared/api/sidecar")

    const response = projectConversationEvents("sess_1", [
      {
        eventId: "acpevt_2",
        sessionId: "sess_1",
        turnId: null,
        sequenceNo: 2,
        eventType: "user_message_chunk",
        createdAt: "2026-04-14T10:01:00Z",
        payload: {
          params: {
            update: {
              sessionUpdate: "user_message_chunk",
              content: {
                type: "text",
                text: "Attached file notes.txt:\nhello from attachment",
                displayText: "Summarize this\nnotes.txt",
                editableText: "Summarize this",
                promptBlocks: [
                  { type: "text", text: "Summarize this" },
                  {
                    type: "resource",
                    resource: {
                      uri: "attachment://session/att_1",
                      mimeType: "text/plain",
                      title: "notes.txt",
                    },
                  },
                ],
              },
            },
          },
        },
      },
    ])

    expect(response[0]).toMatchObject({
      contentText: "Summarize this\nnotes.txt",
      editableText: "Summarize this",
      displayBlocks: [
        { type: "text", text: "Summarize this" },
        { type: "resource", label: "notes.txt", uri: "attachment://session/att_1" },
      ],
    })
  })

  it("projects resource_link labels from prompt blocks", async () => {
    const { projectConversationEvents } = await import("@/shared/api/sidecar")

    const response = projectConversationEvents("sess_1", [
      {
        eventId: "acpevt_3",
        sessionId: "sess_1",
        turnId: null,
        sequenceNo: 3,
        eventType: "user_message_chunk",
        createdAt: "2026-04-14T10:02:00Z",
        payload: {
          params: {
            update: {
              sessionUpdate: "user_message_chunk",
              content: {
                type: "text",
                text: "Please inspect [pyproject.toml](/abs/sidecar/pyproject.toml)",
                displayText: "Inspect pyproject.toml",
                editableText: "Inspect this file",
                promptBlocks: [
                  { type: "text", text: "Inspect this file" },
                  {
                    type: "resource_link",
                    uri: "/abs/sidecar/pyproject.toml",
                    name: "pyproject.toml",
                  },
                ],
              },
            },
          },
        },
      },
    ])

    expect(response[0]).toMatchObject({
      contentText: "Inspect pyproject.toml",
      editableText: "Inspect this file",
      displayBlocks: [
        { type: "text", text: "Inspect this file" },
        { type: "resource", label: "pyproject.toml", uri: "/abs/sidecar/pyproject.toml" },
      ],
    })
  })
})
