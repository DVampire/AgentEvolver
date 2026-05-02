import { SessionMeta } from '../types'
import './SessionList.css'

interface Props {
  sessions: SessionMeta[]
  selected: string | null
  onSelect: (id: string) => void
}

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return iso }
}

export function SessionList({ sessions, selected, onSelect }: Props) {
  if (sessions.length === 0) {
    return <div className="session-empty">No sessions yet.<br/>Start an agent run.</div>
  }

  return (
    <div className="session-list">
      {sessions.map(s => (
        <div
          key={s.session_id}
          className={`session-item ${selected === s.session_id ? 'active' : ''}`}
          onClick={() => onSelect(s.session_id)}
        >
          <div className="session-name">{s.session_id}</div>
          <div className="session-agents">{s.agent_names.join(', ') || '—'}</div>
          <div className="session-footer">
            <span>{s.event_count} events</span>
            <span>{fmtTime(s.last_event_at)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
