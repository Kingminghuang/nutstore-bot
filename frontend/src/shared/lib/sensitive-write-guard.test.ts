import { describe, expect, it } from "vitest"

import { detectSensitiveWriteIssues } from "@/shared/lib"

describe("detectSensitiveWriteIssues", () => {
  it("detects sensitive value patterns in free-text fields", () => {
    const issues = detectSensitiveWriteIssues({
      displayName: 'Team apiKey="sk-sensitive-123456"',
    })

    expect(issues.length).toBeGreaterThan(0)
    expect(issues[0]?.path).toBe("displayName")
  })

  it("does not flag regular provider config text", () => {
    const issues = detectSensitiveWriteIssues({
      displayName: "MiniMax",
      baseUrl: "https://api.minimaxi.com/v1",
      models: [{ modelId: "MiniMax-M2.7-highspeed", displayName: "M2.7" }],
      preferredModelId: "MiniMax-M2.7-highspeed",
    })

    expect(issues).toEqual([])
  })
})
