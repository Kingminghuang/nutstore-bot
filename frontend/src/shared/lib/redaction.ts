const REDACTED = "[REDACTED]"

const SENSITIVE_KEYS = new Set([
  "api_key",
  "apikey",
  "authorization",
  "password",
  "secret",
  "secretvalue",
  "secret_value",
  "token",
])

const keyValuePattern =
  /("?(?:api[_-]?key|authorization|token|secret(?:[_-]?value)?|password)"?\s*[:=]\s*)(".*?"|'.*?'|[^\s,}\]]+)/gi
const bearerPattern = /\bBearer\s+[A-Za-z0-9._-]+/gi
const skPattern = /\bsk-[A-Za-z0-9._-]+/g

export function redactSensitive(value: unknown): unknown {
  if (typeof value === "string") {
    return redactText(value)
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactSensitive(item))
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => {
        const normalized = key.replace(/-/g, "_").toLowerCase()
        if (SENSITIVE_KEYS.has(normalized)) {
          return [key, REDACTED]
        }
        return [key, redactSensitive(item)]
      })
    )
  }
  return value
}

export function redactText(text: string): string {
  return text
    .replace(keyValuePattern, `$1"${REDACTED}"`)
    .replace(bearerPattern, `Bearer ${REDACTED}`)
    .replace(skPattern, REDACTED)
}
