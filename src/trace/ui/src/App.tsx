import { useState, useEffect, useCallback, useRef } from 'react'
import { SessionMeta, TraceEvent } from './types'
import { useWebSocket } from './useWebSocket'
import { SessionList } from './components/SessionList'
import { EventPane } from './components/EventPane'
import { StatusBar } from './components/StatusBar'
import './App.css'

export default function App() {
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [liveEvents, setLiveEvents] = useState<TraceEvent[]>([])
  const [sessionEvents, setSessionEvents] = useState<TraceEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const liveRef = useRef<TraceEvent[]>([])

  const loadSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions')
      const data: SessionMeta[] = await res.json()
      setSessions(data.sort((a, b) =>
        new Date(b.last_event_at).getTime() - new Date(a.last_event_at).getTime()
      ))
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    loadSessions()
    const t = setInterval(loadSessions, 5000)
    return () => clearInterval(t)
  }, [loadSessions])

  const loadSessionEvents = useCallback(async (id: string) => {
    try {
      const data: TraceEvent[] = await fetch(`/api/sessions/${id}`).then(r => r.json())
      setSessionEvents(data)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    if (!selectedSession) { setSessionEvents([]); return }
    loadSessionEvents(selectedSession)
    const t = setInterval(() => loadSessionEvents(selectedSession), 3000)
    return () => clearInterval(t)
  }, [selectedSession, loadSessionEvents])

  const handleLiveEvent = useCallback((ev: TraceEvent) => {
    if (ev._replay) return
    setConnected(true)
    liveRef.current = [...liveRef.current, ev].slice(-2000)
    setLiveEvents([...liveRef.current])
    if (ev.event_type === 'agent_start') setTimeout(loadSessions, 500)
  }, [loadSessions])

  useWebSocket('/ws/live', handleLiveEvent)

  const displayEvents = selectedSession ? sessionEvents : liveEvents

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-title">
          <span className="app-title-icon">⬡</span>
          AgentEvolver Trace
        </div>
        <div className="header-controls">
          <label className="toggle">
            <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} />
            Auto-scroll
          </label>
          <button
            className="btn-header"
            disabled={liveEvents.length === 0}
            onClick={() => { liveRef.current = []; setLiveEvents([]) }}
          >
            Clear
          </button>
        </div>
      </header>

      <div className="app-body">
        <aside className="sidebar">
          <div className="sidebar-header">
            <span>Sessions</span>
            {!selectedSession
              ? <span className="live-badge">Live</span>
              : <button
                  style={{ background:'none', border:'none', color:'var(--accent)', cursor:'pointer', fontSize:11 }}
                  onClick={() => setSelectedSession(null)}
                >← Live</button>
            }
          </div>
          <SessionList sessions={sessions} selected={selectedSession} onSelect={setSelectedSession} />
        </aside>

        <main className="main-pane">
          <EventPane
            events={displayEvents}
            autoScroll={autoScroll && !selectedSession}
            sessionId={selectedSession}
          />
        </main>
      </div>

      <StatusBar
        connected={connected}
        eventCount={liveEvents.length}
        sessionCount={sessions.length}
        selectedSession={selectedSession}
      />
    </div>
  )
}
