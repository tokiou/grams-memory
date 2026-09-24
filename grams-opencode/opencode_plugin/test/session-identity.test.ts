import assert from "node:assert/strict"
import { test } from "node:test"

test("drops unscoped file.edited events and preserves explicit session IDs", async () => {
  const originalEndpoint = process.env.GRAMS_EVENT_ENDPOINT
  const originalFetch = globalThis.fetch
  const originalSetInterval = globalThis.setInterval
  const originalConsoleError = console.error
  const requests: Array<{ body: string }> = []
  const errors: unknown[][] = []
  let responseStatus = 202

  process.env.GRAMS_EVENT_ENDPOINT = "http://receiver/events"
  globalThis.setInterval = (() => 0) as unknown as typeof setInterval
  globalThis.fetch = async (_input, init) => {
    requests.push({ body: String(init?.body ?? "") })
    return new Response(null, { status: responseStatus })
  }
  console.error = (...args: unknown[]) => errors.push(args)

  try {
    const { GramsReceiver } = await import("../src/index.ts")
    const plugin = await GramsReceiver()

    await plugin.event({
      event: {
        id: "event-unscoped",
        type: "file.edited",
        properties: { file: "/app/sol.sql" },
      },
    })
    assert.equal(requests.length, 0)
    assert.ok(errors.some((args) => String(args[0]).includes("no unique active tool session")))

    for (const sessionID of ["default", " ses_invalid"]) {
      await plugin.event({
        event: {
          id: `event-${sessionID}`,
          type: "file.edited",
          sessionID,
          properties: { file: "/app/sol.sql" },
        },
      })
    }
    assert.equal(requests.length, 0)

    await plugin.event({
      event: {
        id: "event-scoped",
        type: "file.edited",
        sessionID: "ses_real-session",
        properties: { file: "/app/sol.sql" },
      },
    })

    assert.equal(requests.length, 1)
    assert.equal(JSON.parse(requests[0].body).session_id, "ses_real-session")

    responseStatus = 422
    await plugin.event({
      event: {
        id: "event-rejected",
        type: "file.edited",
        sessionID: "ses_receiver-rejected",
        properties: { file: "/app/sol.sql" },
      },
    })
    assert.equal(requests.length, 2)
    assert.ok(errors.some((args) => String(args[0]).includes("receiver rejected event")))

    responseStatus = 202
    const eventBodies = () => requests.map(({ body }) => JSON.parse(body))
    await plugin["tool.execute.before"](
      { sessionID: "ses_active-tool", callID: "call-one", tool: "write", args: {} },
      {},
    )
    const beforeInvalidExplicitEditCount = eventBodies().filter(
      (body) => body.type === "FILE_CHANGE_FINAL",
    ).length
    await plugin.event({
      event: {
        id: "event-invalid-explicit-session",
        type: "file.edited",
        sessionID: "default",
        properties: { file: "/app/sol.sql" },
      },
    })
    assert.equal(
      eventBodies().filter((body) => body.type === "FILE_CHANGE_FINAL").length,
      beforeInvalidExplicitEditCount,
    )

    await plugin.event({
      event: {
        id: "event-correlated",
        type: "file.edited",
        properties: { file: "/app/sol.sql" },
      },
    })
    const correlatedEdit = eventBodies().find(
      (body) => body.type === "FILE_CHANGE_FINAL" && body.payload?.id === "event-correlated",
    )
    assert.equal(correlatedEdit?.session_id, "ses_active-tool")

    await plugin["tool.execute.after"](
      { sessionID: "ses_active-tool", callID: "call-one", tool: "write", args: {} },
      {},
    )
    const fileEditCount = eventBodies().filter((body) => body.type === "FILE_CHANGE_FINAL").length
    await plugin.event({
      event: {
        id: "event-after-tool",
        type: "file.edited",
        properties: { file: "/app/sol.sql" },
      },
    })
    assert.equal(
      eventBodies().filter((body) => body.type === "FILE_CHANGE_FINAL").length,
      fileEditCount,
    )

    await plugin["tool.execute.before"](
      { sessionID: "ses_concurrent-a", callID: "call-a", tool: "write", args: {} },
      {},
    )
    await plugin["tool.execute.before"](
      { sessionID: "ses_concurrent-b", callID: "call-b", tool: "write", args: {} },
      {},
    )
    await plugin.event({
      event: {
        id: "event-ambiguous",
        type: "file.edited",
        properties: { file: "/app/sol.sql" },
      },
    })
    assert.equal(
      eventBodies().filter((body) => body.type === "FILE_CHANGE_FINAL").length,
      fileEditCount,
    )
    assert.ok(errors.some((args) => String(args[0]).includes("no unique active tool session")))

    await plugin["tool.execute.after"](
      { sessionID: "ses_concurrent-a", callID: "call-a", tool: "write", args: {} },
      {},
    )
    await plugin["tool.execute.after"](
      { sessionID: "ses_concurrent-b", callID: "call-b", tool: "write", args: {} },
      {},
    )
  } finally {
    console.error = originalConsoleError
    globalThis.fetch = originalFetch
    globalThis.setInterval = originalSetInterval
    if (originalEndpoint === undefined) delete process.env.GRAMS_EVENT_ENDPOINT
    else process.env.GRAMS_EVENT_ENDPOINT = originalEndpoint
  }
})
