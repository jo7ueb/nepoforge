export function normalizeSessionRecord(value) {
  const row = value?.data ?? value?.info ?? value
  const id = row?.id ?? row?.sessionID
  if (typeof id !== "string" || !id) throw new Error("invalid session identity")
  return {
    id,
    parentID: typeof row.parentID === "string" ? row.parentID : undefined,
  }
}

export function failureIdentity(sessionID, callID) {
  if (typeof sessionID !== "string" || !sessionID
      || typeof callID !== "string" || !callID) {
    throw new Error("missing session/tool call identity")
  }
  return JSON.stringify([sessionID, callID])
}

export function isInScope(id, roots, sessions) {
  let current = id
  const seen = new Set()
  while (current && !seen.has(current)) {
    if (roots.has(current)) return true
    seen.add(current)
    current = sessions.get(current)
  }
  return false
}

export async function resolveSessionScope(id, roots, sessions, loadSession, onScoped) {
  const seen = new Set()
  const visit = async (current) => {
    if (roots.has(current)) return true
    if (seen.has(current)) return false
    seen.add(current)
    if (!sessions.has(current)) {
      const row = await loadSession(current)
      if (!row) return undefined
      const session = normalizeSessionRecord(row)
      if (session.id !== current) {
        throw new Error("session lookup identity mismatch")
      }
      sessions.set(session.id, session.parentID)
    }
    const parentID = sessions.get(current)
    return typeof parentID === "string" ? visit(parentID) : false
  }
  const scoped = await visit(id)
  if (scoped) await onScoped(id)
  return scoped
}

export function normalizeUsageEvent(event) {
  const payload = event?.properties ?? {}
  const sessionID = payload.sessionID
  const tokens = payload.tokens
  const tokenParts = tokens && [
    tokens.input,
    tokens.output,
    tokens.reasoning ?? 0,
    tokens.cache?.read ?? 0,
    tokens.cache?.write ?? 0,
  ]
  if (typeof sessionID !== "string" || !sessionID
      || typeof payload.cost !== "number" || !Number.isFinite(payload.cost) || payload.cost < 0
      || !tokenParts || tokenParts.some((count) =>
        typeof count !== "number" || !Number.isFinite(count) || count < 0)) {
    throw new Error("usage event is incomplete")
  }
  if (event.type !== "session.usage.updated"
      && (typeof payload.assistantMessageID !== "string" || !payload.assistantMessageID)) {
    throw new Error("step event is missing assistantMessageID")
  }
  return {
    sessionID,
    assistantMessageID: payload.assistantMessageID,
    costUSD: payload.cost,
    tokens: tokenParts.reduce((total, count) => total + count, 0),
  }
}

export function canAccountUsageEvent(event) {
  try {
    normalizeUsageEvent(event)
    return true
  } catch {
    return false
  }
}

export async function consumeEvents(stream, handleEvent, handleError) {
  for await (const event of stream) {
    try {
      await handleEvent(event)
    } catch (error) {
      await handleError(error, event)
    }
  }
}
