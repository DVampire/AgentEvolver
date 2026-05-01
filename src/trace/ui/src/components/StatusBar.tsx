import './StatusBar.css'

interface Props {
  connected: boolean
  eventCount: number
  sessionCount: number
  selectedSession: string | null
}

export function StatusBar({ connected, eventCount, sessionCount, selectedSession }: Props) {
  return (
    <div className={`status-bar ${connected ? '' : 'disconnected'}`}>
      <div className="status-left">
        <span className="status-dot" />
        <span>{connected ? 'Connected' : 'Disconnected'}</span>
        {selectedSession && (
          <span className="status-session">{selectedSession.slice(0, 28)}</span>
        )}
      </div>
      <div className="status-right">
        <span>{sessionCount} sessions</span>
        <span>·</span>
        <span>{eventCount} live events</span>
      </div>
    </div>
  )
}
