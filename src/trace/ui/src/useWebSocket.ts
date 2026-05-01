import { useEffect, useRef, useCallback } from 'react'
import { TraceEvent } from './types'

type Handler = (event: TraceEvent) => void

export function useWebSocket(
  path: string,
  onEvent: Handler,
  enabled: boolean = true
) {
  const wsRef = useRef<WebSocket | null>(null)
  const handlerRef = useRef<Handler>(onEvent)
  handlerRef.current = onEvent

  const connect = useCallback(() => {
    if (!enabled) return
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const url = `${proto}://${host}${path}`

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'ping') return
        handlerRef.current(data as TraceEvent)
      } catch { /* ignore malformed */ }
    }

    ws.onclose = () => {
      // Reconnect after 2s unless component unmounted
      setTimeout(() => {
        if (wsRef.current === ws) connect()
      }, 2000)
    }

    ws.onerror = () => ws.close()
  }, [path, enabled])

  useEffect(() => {
    connect()
    return () => {
      const ws = wsRef.current
      wsRef.current = null
      ws?.close()
    }
  }, [connect])
}
