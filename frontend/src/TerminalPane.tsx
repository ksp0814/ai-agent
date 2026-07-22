import { useEffect, useRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { listen } from '@tauri-apps/api/event'
import { resizeTerminal, startTerminal, stopTerminal, writeTerminal, type TerminalEvent } from './bridge'
import '@xterm/xterm/css/xterm.css'

type Props = {
  sessionId: string
  workspace: string
}

export function TerminalPane({ sessionId, workspace }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalId = `${workspace}::${sessionId}`

  useEffect(() => {
    if (!containerRef.current) return
    const terminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: '"Cascadia Code", "SFMono-Regular", Consolas, monospace',
      fontSize: 13,
      theme: { background: '#0d0f10', foreground: '#d9e4dd', cursor: '#8be0b7', selectionBackground: '#315b47' },
    })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(containerRef.current)
    fit.fit()
    if (terminal.textarea) {
      terminal.textarea.setAttribute('autocomplete', 'off')
      terminal.textarea.setAttribute('autocorrect', 'off')
      terminal.textarea.setAttribute('autocapitalize', 'off')
      terminal.textarea.setAttribute('spellcheck', 'false')
    }

    let stopped = false
    let unlisten: (() => void) | undefined
    const eventSubscription = listen<TerminalEvent>('terminal-event', (event) => {
      const payload = event.payload
      if (payload.session_id !== terminalId) return
      if (payload.event === 'output' && payload.data) terminal.write(payload.data)
      if (payload.event === 'error' && payload.message) terminal.write(`\r\n\x1b[31m${payload.message}\x1b[0m\r\n`)
      if (payload.event === 'exit') terminal.write('\r\n\x1b[90mCodex 세션이 종료되었습니다.\x1b[0m\r\n')
    })
    void eventSubscription.then((cleanup) => { if (stopped) cleanup(); else unlisten = cleanup })

    const start = async () => {
      try {
        await startTerminal(terminalId, workspace)
        await resizeTerminal(terminalId, terminal.cols, terminal.rows)
      } catch (error) {
        terminal.write(`\x1b[31m터미널을 시작하지 못했습니다: ${String(error)}\x1b[0m\r\n`)
      }
    }
    void start()

    const isTerminalResponse = (data: string) => /^(?:\x1b\[[?0-9;]*c|\x1b\][0-9]+;rgb:[0-9a-f/]+\x1b\\)+$/i.test(data)
    const input = terminal.onData((data) => {
      if (!isTerminalResponse(data)) void writeTerminal(terminalId, data)
    })
    const focusTerminal = () => terminal.focus()
    containerRef.current.addEventListener('mousedown', focusTerminal)
    const resizeObserver = new ResizeObserver(() => {
      fit.fit()
      void resizeTerminal(terminalId, terminal.cols, terminal.rows)
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      stopped = true
      unlisten?.()
      void eventSubscription.then((cleanup) => cleanup())
      input.dispose()
      containerRef.current?.removeEventListener('mousedown', focusTerminal)
      resizeObserver.disconnect()
      terminal.dispose()
      void stopTerminal(terminalId)
    }
  }, [sessionId, workspace])

  return <div ref={containerRef} className="terminal-xterm" aria-label="Codex 터미널" />
}
