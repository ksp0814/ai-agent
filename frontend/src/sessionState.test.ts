import { describe, expect, it } from 'vitest'
import { defaultSessions, loadSessions, saveSessions } from './sessionState'

function memoryStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  }
}

describe('session persistence', () => {
  it('restores valid sessions per workspace', () => {
    const storage = memoryStorage()
    saveSessions('workspace-a', [{ id: 'one', name: '분석' }], storage)
    expect(loadSessions('workspace-a', storage)).toEqual([{ id: 'one', name: '분석' }])
    expect(loadSessions('workspace-b', storage)).toEqual(defaultSessions)
  })

  it('falls back when stored data is invalid', () => {
    const storage = memoryStorage()
    storage.setItem('personal-agent:sessions:workspace-a', '{broken')
    expect(loadSessions('workspace-a', storage)).toEqual(defaultSessions)
  })
})
