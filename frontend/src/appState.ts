export type Workspace = {
  id: string
  name: string
  path: string
  branch: string
}

export type FileEntry = {
  name: string
  kind: 'file' | 'folder'
  children?: FileEntry[]
}

export const demoFiles: FileEntry[] = [
  { name: 'src', kind: 'folder', children: [
    { name: 'personal_agent', kind: 'folder', children: [
      { name: 'bridge.py', kind: 'file' },
      { name: 'terminal.py', kind: 'file' },
      { name: 'tools.py', kind: 'file' },
    ] },
  ] },
  { name: 'tests', kind: 'folder', children: [{ name: 'test_tools.py', kind: 'file' }] },
  { name: 'README.md', kind: 'file' },
  { name: 'pyproject.toml', kind: 'file' },
]

export function filterFiles(entries: FileEntry[], query: string): FileEntry[] {
  const normalized = query.trim().toLowerCase()
  if (!normalized) return entries

  return entries.flatMap((entry) => {
    const children = entry.children ? filterFiles(entry.children, normalized) : []
    if (entry.name.toLowerCase().includes(normalized) || children.length > 0) {
      return [{ ...entry, ...(children.length > 0 ? { children } : {}) }]
    }
    return []
  })
}

export function buildFileTree(files: string[], directories: string[]): FileEntry[] {
  const root: FileEntry[] = []
  const indexes = new WeakMap<FileEntry[], Map<string, FileEntry>>()
  const getIndex = (entries: FileEntry[]) => {
    let index = indexes.get(entries)
    if (!index) {
      index = new Map()
      indexes.set(entries, index)
    }
    return index
  }

  const add = (path: string, kind: FileEntry['kind']) => {
    const parts = path.replaceAll('\\', '/').split('/').filter(Boolean)
    let current = root
    parts.forEach((part, index) => {
      const entriesByName = getIndex(current)
      let entry = entriesByName.get(part)
      const isLeaf = index === parts.length - 1
      if (!entry) {
        entry = { name: part, kind: isLeaf ? kind : 'folder', ...(isLeaf || kind === 'file' ? {} : { children: [] }) }
        current.push(entry)
        entriesByName.set(part, entry)
      }
      if (!isLeaf) {
        entry.kind = 'folder'
        entry.children ??= []
        current = entry.children
      }
    })
  }

  directories.forEach((directory) => add(directory, 'folder'))
  files.forEach((file) => add(file, 'file'))
  const sort = (entries: FileEntry[]): FileEntry[] => entries.sort((a, b) => Number(a.kind === 'file') - Number(b.kind === 'file') || a.name.localeCompare(b.name)).map((entry) => ({ ...entry, ...(entry.children ? { children: sort(entry.children) } : {}) }))
  return sort(root)
}

export function flattenFilePaths(entries: FileEntry[], basePath = ''): string[] {
  return entries.flatMap((entry) => {
    const path = basePath ? `${basePath}/${entry.name}` : entry.name
    return entry.kind === 'folder' ? flattenFilePaths(entry.children ?? [], path) : [path]
  })
}
