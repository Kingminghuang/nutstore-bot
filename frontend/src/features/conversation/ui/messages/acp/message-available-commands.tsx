import type { AvailableCommand } from "@/features/session"

export function MessageAvailableCommands({ commands }: { commands: AvailableCommand[] }) {
  if (commands.length === 0) {
    return null
  }

  return (
    <div className="rounded-2xl border border-[#e8e4e0] bg-[#fcfaf8] px-4 py-3 space-y-2">
      <div className="text-xs uppercase tracking-[0.12em] text-foreground/45">
        Available Commands ({commands.length})
      </div>
      <details className="group" open={false}>
        <summary className="cursor-pointer text-sm text-foreground/72 hover:text-foreground transition-colors">
          Show command list
        </summary>
        <div className="mt-2 space-y-2">
          {commands.map((command) => (
            <div key={command.name} className="rounded-xl border border-[#e8e4e0] bg-background px-3 py-2">
              <div className="text-sm font-medium text-foreground/85">{command.name}</div>
              <div className="text-xs text-foreground/58">
                {command.description}
                {command.hint ? ` (${command.hint})` : ""}
              </div>
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}
