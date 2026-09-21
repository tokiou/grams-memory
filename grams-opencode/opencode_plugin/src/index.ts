type OpenCodeEvent = Record<string, unknown>
type RecordValue = Record<string, unknown>
type PendingIntervention = {
  id: string
  session_id: string
  message: string
  claim_token: string
}

const endpoint = process.env.GRAMS_EVENT_ENDPOINT
const interventionEndpoint = process.env.GRAMS_INTERVENTION_ENDPOINT

async function interventionRequest(
  path: string,
  body: Record<string, unknown>,
): Promise<RecordValue | null> {
  if (!interventionEndpoint) return null
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 5000)
  try {
    const response = await fetch(`${interventionEndpoint}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (!response.ok) {
      console.error("GRAMS intervention request failed", response.status)
      return null
    }
    const value: unknown = await response.json()
    return firstRecord(value)
  } catch (error) {
    console.error("GRAMS intervention service unavailable", error)
    return null
  } finally {
    clearTimeout(timeout)
  }
}

async function claimIntervention(sessionID: string): Promise<PendingIntervention | null> {
  const body = await interventionRequest("/claim", { session_id: sessionID })
  const value = firstRecord(body?.intervention)
  if (
    typeof value.id !== "string" ||
    typeof value.session_id !== "string" ||
    typeof value.message !== "string" ||
    typeof value.claim_token !== "string" ||
    !value.id.trim() ||
    !value.session_id.trim() ||
    !value.claim_token.trim() ||
    value.session_id !== sessionID ||
    !value.message.trim()
  ) {
    return null
  }
  return {
    id: value.id,
    session_id: value.session_id,
    message: value.message,
    claim_token: value.claim_token,
  }
}

async function consumeIntervention(intervention: PendingIntervention): Promise<boolean> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const result = await interventionRequest(
      `/${encodeURIComponent(intervention.id)}/consume`,
      { session_id: intervention.session_id, claim_token: intervention.claim_token },
    )
    if (result?.consumed === true) return true
    if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 100 * (attempt + 1)))
  }
  return false
}

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
  const sessions = new Set<string>()

  const heartbeat = async (): Promise<void> => {
    for (const sessionID of sessions) {
      await emit(normalize(
        "grams.heartbeat",
        { properties: { sessionID, active_tool_call_id: null } },
        "SESSION_HEARTBEAT",
      ))
    }
  }

  const heartbeatTimer = setInterval(() => {
    void heartbeat()
  }, 180_000)
  void heartbeatTimer

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
    "experimental.chat.system.transform": async (
      input: { sessionID?: string },
      output: { system: string[] },
    ) => {
      const sessionID = input.sessionID
      if (!sessionID || !interventionEndpoint) return
      if (!output || !Array.isArray(output.system)) {
        console.error("GRAMS intervention cannot transform malformed system output")
        return
      }
      if (output.system.length > 0 && typeof output.system[0] !== "string") {
        console.error("GRAMS intervention cannot preserve the first system entry")
        return
      }

      const intervention = await claimIntervention(sessionID)
      if (!intervention) return
      try {
        if (output.system.length === 0) {
          output.system[0] = intervention.message
        } else {
          output.system[0] = `${output.system[0]}\n\n${intervention.message}`
        }
      } catch (error) {
        console.error("GRAMS intervention system mutation failed", error)
        return
      }

      if (!await consumeIntervention(intervention)) {
        console.error("GRAMS intervention consumption was not confirmed", intervention.id)
      }
    },

    event: async ({ event }: { event: OpenCodeEvent }) => {
      const kind = String(event.type ?? "unknown")
      const properties = firstRecord(event.properties, event.data)

      if (kind === "message.updated") {
        const info = firstRecord(properties.info)
        const sessionID = typeof info.sessionID === "string" ? info.sessionID : undefined
        const messageID = typeof info.id === "string" ? info.id : undefined
        if (sessionID && messageID) {
          sessions.add(sessionID)
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

        if (sessionID) sessions.add(sessionID)

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
        if (sessionID) {
          sessions.add(sessionID)
          await flushSession(sessionID)
        }
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
      if (typeof input.sessionID === "string") sessions.add(input.sessionID)
    },

    "tool.execute.after": async (
      input: Record<string, unknown>,
      output: Record<string, unknown>,
    ) => {
      await emit(normalize("tool.execute.after", { input, output }, "TOOL_RESULT_FINAL"))
      if (typeof input.sessionID === "string") sessions.add(input.sessionID)
    },
  }
}
