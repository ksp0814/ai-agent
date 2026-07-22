import { describe, expect, it } from 'vitest'
import { buildFileTree, demoFiles, filterFiles } from './appState'

describe('filterFiles', () => {
  it('returns the complete tree for an empty query', () => {
    expect(filterFiles(demoFiles, '')).toEqual(demoFiles)
  })

  it('keeps matching parent folders when a child matches', () => {
    const result = filterFiles(demoFiles, 'terminal')
    expect(result).toEqual([
      { name: 'src', kind: 'folder', children: [
        { name: 'personal_agent', kind: 'folder', children: [{ name: 'terminal.py', kind: 'file' }] },
      ] },
    ])
  })

  it('matches file names without case sensitivity', () => {
    expect(filterFiles(demoFiles, 'README')[0].name).toBe('README.md')
  })
})

describe('buildFileTree', () => {
  it('combines directory and file paths into a sorted tree', () => {
    expect(buildFileTree(['src/tools.py', 'README.md'], ['src'])).toEqual([
      { name: 'src', kind: 'folder', children: [{ name: 'tools.py', kind: 'file' }] },
      { name: 'README.md', kind: 'file' },
    ])
  })
})
