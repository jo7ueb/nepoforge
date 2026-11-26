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
  canAccountUsageEvent,
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
    const getSession = async (sessionID: string) => {
      let timer: ReturnType<typeof setTimeout> | undefined
      try {
        return await Promise.race([
          session.get({ sessionID }),
          new Promise<never>((_resolve, reject) => {
            timer = setTimeout(() => reject(new Error("session lookup timed out")), 2000)
          }),
        ])
      } finally {
        if (timer !== undefined) clearTimeout(timer)
      }
    }
    const resolveScope = (id: string): Promise<boolean | undefined> => resolveSessionScope(
      id,
      roots,
      sessions,
      async (sessionID) => {
        const record = await getSession(sessionID)
        return record ?? undefined
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
      const sessionID = evt?.properties?.sessionID
      if (typeof sessionID !== "string" || !sessionID) {
        throw new Error("usage event is missing sessionID")
      }
      const scoped = await resolveScope(sessionID)
      if (scoped === false) return
      if (scoped !== true) throw new Error("session ancestry unavailable")
      const usage = normalizeUsageEvent(evt)
      const { costUSD, tokens: tokenCount, assistantMessageID } = usage
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
      const result = await getSession(root)
      if (!result) throw new Error("configured root session unavailable")
      track(result)
    }
    for (const id of sessions.keys()) {
      if (inScope(id) && !roots.has(id)) {
        const result = await getSession(id)
        if (result) track(result)
      }
    }

    const registrations = await Promise.all([
      session.hook("retry", async (input) => {
        const retry = input as AnyRecord
        if (inScope(retry.sessionID) && (latched || monitorFailed)) {
          retry.decision = { retry: false }
        }
      }),
      tool.hook("execute.after", async (input) => {
        const result = input as AnyRecord
        if (!inScope(result.sessionID) || result.status !== "error") return
        await recordFailure(result.sessionID, result.id, result.error)
      }),
      tool.hook("execute.before", async (input) => {
        const call = input as AnyRecord
        if (inScope(call.sessionID) && (latched || monitorFailed)) {
          throw new ToolError({ message: "Runtime guard latched; tool execution rejected" })
        }
      }),
    ])

    // v2.4.2 event domain is an AsyncIterable, not a generic `event` hook.
    const handleEvent = async (row: AnyRecord) => {
      if (row.type === "session.usage.updated" || row.type === "session.step.ended") {
        await countUsageEvent(row)
      } else if (row.type === "session.step.failed") {
        // Failed steps may omit cost/token metrics; they are not tool-call failures
        // and incomplete usage must not latch the entire run. Missing spend is an
        // explicit accounting limitation of this monitoring PoC.
        const p = row.properties ?? {}
        if (canAccountUsageEvent(row)) await countUsageEvent(row)
      } else if (row.type === "session.tool.failed") {
        const p = row.properties ?? {}
        if (typeof p.sessionID !== "string") throw new Error("tool failure event is missing sessionID")
        const scoped = await resolveScope(p.sessionID)
        if (scoped === false) return
        if (scoped !== true) throw new Error("tool failure session ancestry unavailable")
        await recordFailure(p.sessionID, p.id, p.error)
      } else if (row.type === "session.created") {
        const p = row.properties ?? {}
        // The global event stream also contains sessions outside this runtime scope.
        // An unrelated malformed creation event must not latch this run.
        if (typeof p.sessionID !== "string" || !p.sessionID) return
        track({ id: p.sessionID, parentID: p.parentID })
      }
    }
    const handleEventError = async (_error: unknown, row: AnyRecord) => {
      // Only latch if the event is known or could not be proven outside this scope.
      // A malformed event from a confirmed unrelated session is ignored.
      if (typeof row?.properties?.sessionID === "string") {
        try {
          if (await resolveScope(row.properties.sessionID) === false) return
        } catch {
          // Keep the failure latched without guessing that an unrelated session is in scope.
        }
      }
      monitorFailed = true
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
