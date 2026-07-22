export type AgentSession = {
  id: string
  name: string
}

export const defaultSessions: AgentSession[] = [{ id: 'session-1', name: '세션 1' }]

type StorageLike = {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
}

const storageKey = (workspace: string) => `personal-agent:sessions:${workspace}`

export function loadSessions(workspace: string, storage?: StorageLike): AgentSession[] {
  if (!storage) return defaultSessions
  try {
    const parsed = JSON.parse(storage.getItem(storageKey(workspace)) ?? 'null')
    if (!Array.isArray(parsed)) return defaultSessions
    const sessions = parsed.filter((value): value is AgentSession => Boolean(value && typeof value.id === 'string' && typeof value.name === 'string' && value.id && value.name.trim()))
    return sessions.length ? sessions : defaultSessions
  } catch {
    return defaultSessions
  }
}

export function saveSessions(workspace: string, sessions: AgentSession[], storage?: StorageLike): void {
  if (!storage) return
  storage.setItem(storageKey(workspace), JSON.stringify(sessions))
}
