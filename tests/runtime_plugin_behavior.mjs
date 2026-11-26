import assert from "node:assert/strict"
import {
  consumeEvents,
  failureIdentity,
  isInScope,
  normalizeSessionRecord,
  normalizeUsageEvent,
  resolveSessionScope,
} from "../nepoforge/data/plugins/runtime-guard-core.mjs"

const created = normalizeSessionRecord({ sessionID: "child", parentID: "root" })
assert.deepEqual(created, { id: "child", parentID: "root" })
assert.equal(normalizeSessionRecord({ data: { id: "root" } }).id, "root")

const roots = new Set(["root"])
const parents = new Map([["child", "root"], ["grandchild", "child"], ["outside", undefined]])
assert.equal(isInScope("grandchild", roots, parents), true)
assert.equal(isInScope("outside", roots, parents), false)
assert.equal(isInScope("missing", roots, parents), false)
const partiallyKnown = new Map([["child", "middle"]])
const fetched = []
const resolved = await resolveSessionScope(
  "child",
  roots,
  partiallyKnown,
  async (id) => {
    fetched.push(id)
    if (id === "middle") return { sessionID: "middle", parentID: "root" }
    return undefined
  },
  async () => {},
)
assert.equal(resolved, true)
assert.deepEqual(fetched, ["middle"])
assert.equal(partiallyKnown.get("middle"), "root")

// Hook and event both identify a failure by the OpenCode tool-call ID.
assert.equal(failureIdentity("root", "call-1"), failureIdentity("root", "call-1"))
assert.notEqual(failureIdentity("root", "call-1"), failureIdentity("child", "call-1"))

const usage = normalizeUsageEvent({
  type: "session.usage.updated",
  properties: {
    sessionID: "root",
    cost: 0.25,
    tokens: { input: 10, output: 5, reasoning: 2, cache: { read: 3, write: 0 } },
  },
})
assert.deepEqual(usage, {
  sessionID: "root", assistantMessageID: undefined, costUSD: 0.25, tokens: 20,
})
assert.throws(() => normalizeUsageEvent({
  type: "session.step.failed",
  properties: { sessionID: "root", assistantMessageID: "message-1", error: { name: "Failure" } },
}), /incomplete/)

const handled = []
const errors = []
async function* stream() {
  yield { type: "malformed" }
  yield { type: "session.created", properties: { sessionID: "root" } }
}
await consumeEvents(
  stream(),
  async (event) => {
    if (event.type === "malformed") throw new Error("bad event")
    handled.push(event.type)
  },
  async (error, event) => errors.push([error.message, event.type]),
)
assert.deepEqual(errors, [["bad event", "malformed"]])
assert.deepEqual(handled, ["session.created"])
