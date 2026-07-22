import { invoke } from '@tauri-apps/api/core'

export type BackendStatus = {
  status: string
  transport: string
}

export async function getBackendStatus(): Promise<BackendStatus> {
  if (!('__TAURI_INTERNALS__' in window)) {
    return { status: 'browser-preview', transport: 'vite' }
  }
  return invoke<BackendStatus>('backend_status')
}

export type WorkspaceSnapshot = {
  files: string[]
  directories: string[]
  git: { available: boolean; branch: string; entries: Record<string, string> }
}

export async function getWorkspaceSnapshot(workspace: string): Promise<WorkspaceSnapshot> {
  return invoke<WorkspaceSnapshot>('bridge_request', {
    workspace,
    request: { method: 'workspace_snapshot' },
  })
}

export type UsageSnapshot = {
  rateLimits?: { primary?: { usedPercent?: number; resetsAt?: number } }
  primary?: { usedPercent?: number; resetsAt?: number }
  rateLimitResetCredits?: { availableCount?: number; credits?: Array<{ id?: string }> }
}

export async function getUsageSnapshot(workspace: string): Promise<UsageSnapshot> {
  return invoke<UsageSnapshot>('bridge_request', {
    workspace,
    request: { method: 'usage_snapshot' },
  })
}

export function resetUsage(workspace: string, creditId = '') {
  return invoke<{ outcome: string }>('bridge_request', {
    workspace,
    request: { method: 'usage_reset', creditId },
  })
}

export type FileContent = { path: string; content: string }

export async function readFile(workspace: string, path: string): Promise<FileContent> {
  return invoke<FileContent>('bridge_request', {
    workspace,
    request: { method: 'read_file', path },
  })
}

export async function readDiff(workspace: string, path: string): Promise<FileContent> {
  return invoke<FileContent>('bridge_request', {
    workspace,
    request: { method: 'git_diff_file', path },
  })
}

export function approveFile(workspace: string, path: string) {
  return invoke<{ path: string; action: string }>('bridge_request', {
    workspace,
    request: { method: 'approve_file', path },
  })
}

export function rollbackFile(workspace: string, path: string) {
  return invoke<{ path: string; action: string; location: string }>('bridge_request', {
    workspace,
    request: { method: 'rollback_file', path },
  })
}

export type TerminalEvent = {
  session_id: string
  event: 'ready' | 'output' | 'error' | 'exit' | 'stopped'
  data?: string
  message?: string
}

export function startTerminal(sessionId: string, workspace: string) {
  return invoke<void>('start_terminal', { sessionId, workspace })
}

export function writeTerminal(sessionId: string, text: string) {
  return invoke<void>('write_terminal', { sessionId, text })
}

export function resizeTerminal(sessionId: string, columns: number, rows: number) {
  return invoke<void>('resize_terminal', { sessionId, columns, rows })
}

export function stopTerminal(sessionId: string) {
  return invoke<void>('stop_terminal', { sessionId })
}
