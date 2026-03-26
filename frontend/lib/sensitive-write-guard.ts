const SENSITIVE_KEYS = [
  "api_key",
  "apikey",
  "authorization",
  "password",
  "secret",
  "secretvalue",
  "secret_value",
  "token",
]

const SENSITIVE_VALUE_PATTERNS = [
  /\bbearer\s+[A-Za-z0-9._-]{8,}/i,
  /\bsk-[A-Za-z0-9._-]{8,}/,
  /(?:api[_-]?key|authorization|token|secret(?:[_-]?value)?|password)\s*[:=]\s*['"]?[A-Za-z0-9._-]{6,}/i,
]

type SensitiveIssue = {
  path: string
  reason: "sensitive_key" | "sensitive_value"
}

export function detectSensitiveWriteIssues(value: unknown): SensitiveIssue[] {
  const issues: SensitiveIssue[] = []
  walk(value, [], issues)
  return issues
}

function walk(value: unknown, path: string[], issues: SensitiveIssue[]) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walk(item, [...path, String(index)], issues))
    return
  }

  if (value && typeof value === "object") {
    Object.entries(value).forEach(([key, nested]) => {
      const normalizedKey = key.replace(/-/g, "_").toLowerCase()
      const nextPath = [...path, key]
      if (SENSITIVE_KEYS.includes(normalizedKey) && nested !== "" && nested != null) {
        issues.push({ path: nextPath.join("."), reason: "sensitive_key" })
      }
      walk(nested, nextPath, issues)
    })
    return
  }

  if (typeof value !== "string") {
    return
  }

  if (SENSITIVE_VALUE_PATTERNS.some((pattern) => pattern.test(value))) {
    issues.push({ path: path.join("."), reason: "sensitive_value" })
  }
}
