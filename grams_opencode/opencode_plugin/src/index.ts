type OpenCodeEvent = Record<string, unknown>

const endpoint = process.env.GRAMS_EVENT_ENDPOINT

function firstRecord(...values: unknown[]): Record<string, unknown> {
  return values.find(
    (value): value is Record<string, unknown> =>
      typeof value === "object" && value !== null && !Array.isArray(value),
  ) ?? {}
}

function normalize(kind: string, value: unknown): OpenCodeEvent {
  const event = firstRecord(value)
  const properties = firstRecord(event.properties, event.data)
  const part = firstRecord(properties.part, event.part)
  const partType = typeof part.type === "string" ? part.type : undefined
  const normalizedKind =
    kind === "message.part.updated" && partType === "reasoning"
      ? "REASONING"
      : kind === "message.part.updated" && partType === "text"
        ? "TEXT"
        : kind === "file.edited"
          ? "FILE_CHANGE"
          : kind === "tool.execute.before"
            ? "TOOL_CALL"
            : kind === "tool.execute.after"
              ? "TOOL_RESULT"
              : kind.toUpperCase().replaceAll(".", "_")

  return {
    schema_version: 1,
    type: normalizedKind,
    source_event: kind,
    timestamp: new Date().toISOString(),
    session_id: event.sessionID ?? properties.sessionID ?? null,
    payload: value,
  }
}

async function emit(event: OpenCodeEvent): Promise<void> {
  if (!endpoint) return

  try {
    await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(event),
    })
  } catch (error) {
    console.error("GRAMS receiver unavailable", error)
  }
}

export const GramsReceiver = async () => ({
  event: async ({ event }: { event: OpenCodeEvent }) => {
    await emit(normalize(String(event.type ?? "unknown"), event))
  },

  "tool.execute.before": async (
    input: Record<string, unknown>,
    output: Record<string, unknown>,
  ) => {
    await emit(normalize("tool.execute.before", { input, output }))
  },

  "tool.execute.after": async (
    input: Record<string, unknown>,
    output: Record<string, unknown>,
  ) => {
    await emit(normalize("tool.execute.after", { input, output }))
  },
})
