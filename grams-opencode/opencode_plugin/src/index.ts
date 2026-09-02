type OpenCodeEvent = Record<string, unknown>
type RecordValue = Record<string, unknown>

const endpoint = process.env.GRAMS_EVENT_ENDPOINT

function firstRecord(...values: unknown[]): RecordValue {
  return values.find(
    (value): value is RecordValue =>
      typeof value === "object" && value !== null && !Array.isArray(value),
  ) ?? {}
}

function normalize(
  kind: string,
  value: unknown,
  type = kind.toUpperCase().replaceAll(".", "_"),
): OpenCodeEvent {
  const event = firstRecord(value)
  const properties = firstRecord(event.properties, event.data)
  const part = firstRecord(properties.part, event.part)
  const input = firstRecord(event.input)

  return {
    schema_version: 1,
    type,
    source_event: kind,
    timestamp: new Date().toISOString(),
    session_id: event.sessionID ?? properties.sessionID ?? part.sessionID ?? input.sessionID ?? null,
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

export const GramsReceiver = async () => {
  const pendingParts = new Map<string, Map<string, OpenCodeEvent>>()
  const finalizedParts = new Set<string>()
  const assistantMessages = new Map<string, Set<string>>()
  const userMessages = new Map<string, Set<string>>()

  const partKey = (sessionID: string, part: OpenCodeEvent): string =>
    `${sessionID}:${String(part.id ?? "unknown")}`

  const rememberPart = (sessionID: string, part: OpenCodeEvent): void => {
    const parts = pendingParts.get(sessionID) ?? new Map<string, OpenCodeEvent>()
    const partID = typeof part.id === "string" ? part.id : crypto.randomUUID()
    parts.set(partID, part)
    pendingParts.set(sessionID, parts)
  }

  const messageSet = (
    messages: Map<string, Set<string>>,
    sessionID: string,
  ): Set<string> => {
    const ids = messages.get(sessionID) ?? new Set<string>()
    messages.set(sessionID, ids)
    return ids
  }

  const flushSession = async (sessionID: string): Promise<void> => {
    const parts = pendingParts.get(sessionID)
    if (parts) {
      for (const part of parts.values()) {
        const partType = part.type
        const messageID = typeof part.messageID === "string" ? part.messageID : undefined
        const key = partKey(sessionID, part)
        const finalType = messageID && userMessages.get(sessionID)?.has(messageID)
          ? "USER_MESSAGE_FINAL"
          : messageID && assistantMessages.get(sessionID)?.has(messageID)
            ? `${String(partType).toUpperCase()}_FINAL`
            : undefined
        if (
          messageID &&
          finalType &&
          (partType === "text" || partType === "reasoning") &&
          !finalizedParts.has(key)
        ) {
          await emit(normalize(
            "message.part.updated",
            { properties: { part } },
            finalType,
          ))
          finalizedParts.add(key)
        }
      }
      pendingParts.delete(sessionID)
      assistantMessages.delete(sessionID)
      userMessages.delete(sessionID)
      for (const key of finalizedParts) {
        if (key.startsWith(`${sessionID}:`)) finalizedParts.delete(key)
      }
    }

    await emit(normalize("session.idle", { properties: { sessionID } }, "MESSAGE_COMPLETED"))
  }

  return {
    event: async ({ event }: { event: OpenCodeEvent }) => {
      const kind = String(event.type ?? "unknown")
      const properties = firstRecord(event.properties, event.data)

      if (kind === "message.updated") {
        const info = firstRecord(properties.info)
        const sessionID = typeof info.sessionID === "string" ? info.sessionID : undefined
        const messageID = typeof info.id === "string" ? info.id : undefined
        if (sessionID && messageID) {
          const messages = info.role === "assistant" ? assistantMessages : userMessages
          messageSet(messages, sessionID).add(messageID)
        }
        return
      }

      if (kind === "message.part.updated") {
        const part = firstRecord(properties.part, event.part)
        const partType = part.type
        const sessionID = typeof part.sessionID === "string"
          ? part.sessionID
          : typeof properties.sessionID === "string"
            ? properties.sessionID
            : undefined

        const messageID = typeof part.messageID === "string" ? part.messageID : undefined
        const isKnownMessage = Boolean(
          sessionID &&
          messageID &&
          (
            assistantMessages.get(sessionID)?.has(messageID) ||
            userMessages.get(sessionID)?.has(messageID)
          ),
        )

        if (sessionID && (partType === "text" || partType === "reasoning")) {
          rememberPart(sessionID, part)
          const time = firstRecord(part.time)
          const key = partKey(sessionID, part)
          const finalType = messageID && userMessages.get(sessionID)?.has(messageID)
            ? "USER_MESSAGE_FINAL"
            : messageID && assistantMessages.get(sessionID)?.has(messageID)
              ? `${String(partType).toUpperCase()}_FINAL`
              : undefined
          if (isKnownMessage && finalType && typeof time.end === "number" && !finalizedParts.has(key)) {
            await emit(normalize(
              kind,
              { properties: { part } },
              finalType,
            ))
            finalizedParts.add(key)
            pendingParts.get(sessionID)?.delete(String(part.id))
          }
        }
        return
      }

      if (kind === "message.part.delta" || kind === "tool.execute.before") return

      if (kind === "tool.execute.after") return

      if (kind === "session.idle") {
        const sessionID = typeof properties.sessionID === "string" ? properties.sessionID : undefined
        if (sessionID) await flushSession(sessionID)
        return
      }

      if (kind === "file.edited") {
        await emit(normalize(kind, event, "FILE_CHANGE_FINAL"))
        return
      }

      if (kind === "session.error") {
        await emit(normalize(kind, event, "MESSAGE_ERROR"))
      }
    },

    "tool.execute.before": async (
      input: Record<string, unknown>,
      output: Record<string, unknown>,
    ) => {
      await emit(normalize("tool.execute.before", { input, output }, "TOOL_CALL_FINAL"))
    },

    "tool.execute.after": async (
      input: Record<string, unknown>,
      output: Record<string, unknown>,
    ) => {
      await emit(normalize("tool.execute.after", { input, output }, "TOOL_RESULT_FINAL"))
    },
  }
}
