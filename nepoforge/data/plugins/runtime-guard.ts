/**
 * OpenCode 2.4.2 plugin API PoC.
 *
 * This uses the v2.4.2 @opencode/plugin setup/hook API. Cost thresholds are USD
 * (Money.USD); they are not linked to Nepoforge's yen budget or price table.
 * Report/IPC and recovery for missed events are absent.
 * This is not a gate for all new model requests and cannot protect if unloaded.
 * The public plugin Context exposes no message.list method; event gaps therefore
 * cannot be repaired by polling through this API and remain an explicit limitation.
 */
import type { Plugin as PluginTypes } from "@opencode/plugin"
import { Error as ToolError } from "@opencode/plugin/promise/tool"
import {
  consumeEvents,
  failureIdentity,
  isInScope,
  normalizeSessionRecord,
  normalizeUsageEvent,
  resolveSessionScope,
} from "./runtime-guard-core.mjs"

type AnyRecord = Record<string, any>

export const RuntimeGuardPlugin: PluginTypes.Plugin = {
  id: "nepoforge-runtime-guard",
  setup: async ({ session, tool, event }) => {
    const sessions = new Map<string, string | undefined>()
    const failures = new Map<string, number>()
    const handledFailures = new Set<string>()
    const interruptResults = new Map<string, {
      requested: true; status: "pending" | "settled" | "error"; interrupted?: boolean; error?: string
    }>()
    const sessionUsage = new Map<string, { costUSD: number; tokens: number }>()
    const stepUsage = new Map<string, { sessionID: string; costUSD: number; tokens: number }>()
    const roots = new Set((process.env.NEPOFORGE_RUNTIME_GUARD_SESSIONS ?? "")
      .split(",").map((s) => s.trim()).filter(Boolean))
    const maxCostUSD = Number(process.env.NEPOFORGE_RUNTIME_GUARD_MAX_COST_USD)
    const maxTokens = Number(process.env.NEPOFORGE_RUNTIME_GUARD_MAX_TOKENS)
    const enabled = roots.size > 0 && Number.isFinite(maxCostUSD) && maxCostUSD > 0
      && Number.isFinite(maxTokens) && maxTokens > 0
    let latched = false
    let monitorFailed = false

    const inScope = (id: string) => isInScope(id, roots, sessions)
    const interruptOne = async (id: string) => {
      if (interruptResults.has(id)) return
      // If the request never settles, leave its outcome explicitly pending/unknown.
      interruptResults.set(id, { requested: true, status: "pending" })
      try {
        const response = await session.interrupt({ sessionID: id, resume: false })
        interruptResults.set(id, {
          requested: true, status: "settled", interrupted: response.interrupted,
        })
      } catch {
        monitorFailed = true
        interruptResults.set(id, {
          requested: true, status: "error", error: "interrupt request failed",
        })
      }
    }
    const latch = () => {
      if (latched) return
      latched = true
      const targets = [...sessions.keys()].filter(inScope)
      const requests = targets.map((id) => interruptOne(id))
      // Fan out first: rejection or a never-settling request cannot stop dispatch to later targets.
      // Do not block event consumption on a stalled interrupt; pending is not confirmed stopped.
      void Promise.allSettled(requests)
    }
    const track = (row: AnyRecord) => {
      const session = normalizeSessionRecord(row)
      sessions.set(session.id, session.parentID)
      if (latched && inScope(session.id)) void interruptOne(session.id)
    }
    const resolveScope = (id: string) => resolveSessionScope(
      id,
      roots,
      sessions,
      async (sessionID) => {
        const record = await session.get({ sessionID })
        return record ? normalizeSessionRecord(record) : undefined
      },
      async (sessionID) => {
        if (latched) await interruptOne(sessionID)
      },
    )
    const totals = () => {
      const sessionIDs = new Set([...sessionUsage.keys(), ...[...stepUsage.values()].map((v) => v.sessionID)])
      let costUSD = 0
      let tokens = 0
      for (const sessionID of sessionIDs) {
        const cumulative = sessionUsage.get(sessionID)
        if (cumulative) {
          // session.usage.updated is cumulative and authoritative for that session.
          // Use message-level step records only until a session cumulative event arrives.
          costUSD += cumulative.costUSD
          tokens += cumulative.tokens
          continue
        }
        for (const item of stepUsage.values()) {
          if (item.sessionID === sessionID) {
            costUSD += item.costUSD
            tokens += item.tokens
          }
        }
      }
      return { costUSD, tokens }
    }
    const checkLimits = async () => {
      const total = totals()
      if (total.costUSD >= maxCostUSD || total.tokens >= maxTokens) await latch()
    }
    const recordFailure = async (sessionID: string, callID: string, error: unknown) => {
      const identity = failureIdentity(sessionID, callID)
      if (handledFailures.has(identity)) return
      handledFailures.add(identity)
      const text = String((error as AnyRecord)?.message ?? error ?? "").toLowerCase()
      const fingerprint = text.includes("oldstring") || text.includes("old string")
        ? "oldstring-mismatch"
        : text.includes("permission") || text.includes("denied") ? "permission-denied"
        : text.includes("invalid argument") ? "invalid-arguments"
        : text.includes("timeout") || text.includes("connection") ? "transport-error"
        : "tool-error"
      const key = `${sessionID}:${fingerprint}`
      const count = (failures.get(key) ?? 0) + 1
      failures.set(key, count)
      if (count >= 3) await latch()
    }
    const countUsageEvent = async (evt: AnyRecord) => {
      const usage = normalizeUsageEvent(evt)
      const { sessionID, costUSD, tokens: tokenCount, assistantMessageID } = usage
      if (!(await resolveScope(sessionID))) throw new Error("session ancestry unavailable")
      if (evt.type === "session.usage.updated") {
        // Session cumulative source has no assistantMessageID; replace by session.
        sessionUsage.set(sessionID, { costUSD, tokens: tokenCount })
      } else {
        // Step events are per assistant message. Repeated terminal events replace by identity.
        stepUsage.set(`${sessionID}:${assistantMessageID}`, {
          sessionID, costUSD, tokens: tokenCount,
        })
      }
      void checkLimits()
    }

    if (!enabled) return
    // Explicit session roots are the only scope. Discover their descendants through list/get.
    const listed = await session.list()
    for (const item of listed) track(item)
    for (const root of roots) {
      const result = await session.get({ sessionID: root })
      if (!result) throw new Error("configured root session unavailable")
      track(result)
    }
    for (const id of sessions.keys()) {
      if (inScope(id) && !roots.has(id)) {
        const result = await session.get({ sessionID: id })
        if (result) track(result)
      }
    }

    const registrations = await Promise.all([
      session.hook("retry", async (input) => {
        const retry = input as AnyRecord
        if ((await resolveScope(retry.sessionID)) && (latched || monitorFailed)) {
          retry.decision = { retry: false }
        }
      }),
      tool.hook("execute.after", async (input) => {
        const result = input as AnyRecord
        if (!(await resolveScope(result.sessionID)) || result.status !== "error") return
        await recordFailure(result.sessionID, result.id, result.error)
      }),
      tool.hook("execute.before", async (input) => {
        const call = input as AnyRecord
        if ((await resolveScope(call.sessionID)) && (latched || monitorFailed)) {
          throw new ToolError({ message: "Runtime guard latched; tool execution rejected" })
        }
      }),
    ])

    // v2.4.2 event domain is an AsyncIterable, not a generic `event` hook.
    const handleEvent = async (row: AnyRecord) => {
      if (row.type === "session.usage.updated" || row.type === "session.step.ended"
          || row.type === "session.step.failed") {
        await countUsageEvent(row)
      } else if (row.type === "session.tool.failed") {
        const p = row.properties ?? {}
        if (typeof p.sessionID !== "string" || !(await resolveScope(p.sessionID))) return
        await recordFailure(p.sessionID, p.id, p.error)
      } else if (row.type === "session.created") {
        const p = row.properties ?? {}
        track({ id: p.sessionID, parentID: p.parentID })
      }
    }
    const handleEventError = async (_error: unknown, row: AnyRecord) => {
      // A malformed or out-of-order event degrades this run and latches known in-scope
      // sessions, but does not terminate the event stream for later observations.
      monitorFailed = true
      if (typeof row?.properties?.sessionID === "string") {
        try {
          await resolveScope(row.properties.sessionID)
        } catch {
          // Keep the failure latched without guessing that an unrelated session is in scope.
        }
      }
      latch()
    }
    const monitor = (async () => {
      try {
        await consumeEvents(event.subscribe(), handleEvent, handleEventError)
        throw new Error("event stream ended")
      } catch {
        monitorFailed = true
        latch()
      }
    })()

    return async () => {
      for (const registration of registrations) await registration.dispose()
      void monitor; void interruptResults
    }
  },
}

export default RuntimeGuardPlugin
