import { useEffect, useRef, useState, useMemo } from 'react'
import { TraceEvent } from '../types'
import './EventPane.css'

interface Props {
  events: TraceEvent[]
  autoScroll: boolean
  sessionId: string | null
}

function fmtMs(ms?: number | null) {
  if (ms == null) return ''
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`
}
function fmtTime(iso: string) {
  try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return '' }
}

// ── Render value as readable text ─────────────────────────────────────────────
function renderValue(data: unknown): string {
  if (data == null) return ''
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data.map(renderValue).join('\n')
  if (typeof data === 'object') {
    const entries = Object.entries(data as Record<string, unknown>)
    // single string value — just show the value
    if (entries.length === 1 && typeof entries[0][1] === 'string') return entries[0][1]
    // multi-key: "key: value" lines
    return entries
      .map(([k, v]) => {
        const val = typeof v === 'string' ? v : JSON.stringify(v)
        return `${k}: ${val}`
      })
      .join('\n')
  }
  return String(data)
}

// ── Pair call+result ──────────────────────────────────────────────────────────
interface ToolPair { call: TraceEvent; result?: TraceEvent }

interface StepGroup {
  stepNum: number
  startTime: string
  duration?: number
  thinking?: string
  nextGoal?: string
  pairs: ToolPair[]
}
interface AgentGroup {
  start: TraceEvent
  steps: StepGroup[]
  done?: TraceEvent
}

function groupEvents(events: TraceEvent[]): AgentGroup[] {
  const agents: AgentGroup[] = []
  let curAgent: AgentGroup | null = null
  let curStep: StepGroup | null = null

  for (const ev of events) {
    const t = ev.event_type

    if (t === 'agent_start') {
      curAgent = { start: ev, steps: [] }
      agents.push(curAgent)
      curStep = null
      continue
    }
    if (t === 'agent_end') {
      if (curAgent) curAgent.done = ev
      curStep = null
      continue
    }
    if (t === 'step_start') {
      curStep = { stepNum: ev.step_number ?? 0, startTime: ev.timestamp, pairs: [] }
      curAgent?.steps.push(curStep)
      continue
    }
    if (t === 'step_end') {
      if (curStep) {
        curStep.duration = ev.duration_ms ?? undefined
        // thinking lives in output.thinking
        const out = ev.output as any
        curStep.thinking = out?.thinking ?? undefined
        curStep.nextGoal = out?.next_goal ?? undefined
      }
      continue
    }
    if (t === 'tool_call' || t === 'action_start' || t === 'skill_call') {
      if (curStep) curStep.pairs.push({ call: ev })
      continue
    }
    if (t === 'tool_result' || t === 'action_end' || t === 'skill_result') {
      if (curStep) {
        const pair = [...curStep.pairs].reverse().find(
          p => p.call.action_index === ev.action_index && !p.result
        )
        if (pair) pair.result = ev
        else curStep.pairs.push({ call: { ...ev, event_type: 'tool_call', input: undefined, output: undefined }, result: ev })
      }
      continue
    }
  }

  return agents
}

// ── Components ────────────────────────────────────────────────────────────────

function Thinking({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="ep-thinking">
      <div className="ep-thinking-header" onClick={() => setOpen(o => !o)}>
        <span className="ep-thinking-toggle">{open ? '▾' : '▸'}</span>
        <span className="ep-thinking-label">Thinking</span>
        {!open && <span className="ep-thinking-preview">{text.slice(0, 160)}</span>}
      </div>
      {open && <div className="ep-thinking-body">{text}</div>}
    </div>
  )
}

function buildDescText(input: unknown): string {
  if (input == null) return ''
  if (typeof input === 'string') return input.slice(0, 100)
  if (typeof input === 'object') {
    return Object.entries(input as Record<string, unknown>)
      .slice(0, 3)
      .map(([k, v]) => `${k}: ${JSON.stringify(v)}`.slice(0, 50))
      .join('  ')
  }
  return String(input).slice(0, 100)
}

function IoRow({ tag, data, isError }: { tag: string; data: unknown; isError?: boolean }) {
  const text = renderValue(data)
  return (
    <div className="ep-io-row">
      <div className={`ep-io-tag ${tag}-tag`}>{tag}</div>
      <div className={`ep-io-content${isError ? ' err-content' : ''}`}>{text}</div>
    </div>
  )
}

function ToolCard({ pair, defaultOpen }: { pair: ToolPair; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const { call, result } = pair
  const name = call.action_name || call.label
  const isError = !!(result?.error) || (result?.metadata && (result.metadata as any).success === false)
  const hasResult = !!result
  const hasBody = call.input != null || result?.output != null || result?.error

  return (
    <div className={`ep-tool${isError ? ' ep-error' : ''}${!hasResult ? ' ep-running' : ''}`}>
      <div className="ep-tool-header" onClick={() => setOpen(o => !o)}>
        <div className="ep-tool-toggle">{open ? '▾' : '▸'}</div>
        <span className="ep-tool-name">{name}</span>
        <span className="ep-tool-desc">{buildDescText(call.input)}</span>
        <div className="ep-tool-right">
          {hasResult && !isError && <span className="ep-status ep-status-ok">✓</span>}
          {isError && <span className="ep-status ep-status-err">✗</span>}
          {!hasResult && <span className="ep-status ep-status-run">…</span>}
          {result?.duration_ms != null && <span className="ep-dur">{fmtMs(result.duration_ms)}</span>}
          <span className="ep-time">{fmtTime(call.timestamp)}</span>
        </div>
      </div>
      {open && hasBody && (
        <div className="ep-tool-body">
          {call.input != null && <IoRow tag="in" data={call.input} />}
          {result?.output != null && <IoRow tag="out" data={result.output} />}
          {result?.error && <IoRow tag="err" data={result.error} isError />}
        </div>
      )}
    </div>
  )
}

function StepBlock({ step }: { step: StepGroup }) {
  const [open, setOpen] = useState(true)

  return (
    <div>
      <div className="ep-step" onClick={() => setOpen(o => !o)} style={{ cursor: 'pointer' }}>
        <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>{open ? '▾' : '▸'}</span>
        <span className="ep-step-label">Step {step.stepNum}</span>
        <span className="ep-step-time">{fmtTime(step.startTime)}</span>
        {step.duration != null && <span className="ep-step-dur">{fmtMs(step.duration)}</span>}
      </div>
      {open && (
        <>
          {step.thinking && <Thinking text={step.thinking} />}
          {step.pairs.map((pair, pi) => (
            <ToolCard key={pi} pair={pair} defaultOpen={true} />
          ))}
        </>
      )}
    </div>
  )
}

// ── EventPane ─────────────────────────────────────────────────────────────────

export function EventPane({ events, autoScroll, sessionId }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const paneRef = useRef<HTMLDivElement>(null)

  const agents = useMemo(() => groupEvents(events), [events])

  // Auto-scroll: track events.length changes
  useEffect(() => {
    if (!autoScroll) return
    const el = paneRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [events.length, autoScroll])

  if (events.length === 0) {
    return <div className="event-pane-empty">{sessionId ? 'No events.' : '$ waiting for agent…'}</div>
  }

  return (
    <div className="event-pane" ref={paneRef}>
      {agents.map((ag, ai) => (
        <div key={ai}>
          <div className="ep-agent">
            <div className="ep-agent-name">🤖 {ag.start.agent_name}</div>
            {(ag.start.input as any)?.task && (
              <div className="ep-agent-task">{(ag.start.input as any).task}</div>
            )}
          </div>

          {ag.steps.map((step, si) => (
            <StepBlock key={si} step={step} />
          ))}

          {ag.done && (
            <div className="ep-done">✓ done · {fmtMs(ag.done.duration_ms)}</div>
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
