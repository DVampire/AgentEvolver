import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, Check, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown,
  Copy, Layers, Loader2, Terminal, Wrench,
} from 'lucide-react';

import { Button } from '../components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import type { RequestFn } from '../canvas/types';
import type { GatewayEvent } from '../controllers/gateway';

/* ===========================================================================
   The trajectory: everything the conversation column throws away.

   The column reduces each trace event to one line — what acted, on what — and
   drops the reasoning behind it, the arguments, the result, the cost and the
   time. All of that is already on the wire (`trace.event`) and on disk
   (`conversation.events`); this dialog is where it is read.

   The projection below is deliberately the same one the backend performs in
   `agentevolver/trace/derive.py`: the surface decides which events still stand
   for the history, an `agent_call` is the assistant's turn carrying the calls
   its step made, and each `tool_call` / `skill_call` is that turn's result,
   matched by call id. Two views of one log that disagree are worse than one
   view, so where derive.py makes a choice, this makes the same one and says so.
   =========================================================================== */

/** A trace event as it arrives — `TraceEvent.to_dict()`, see trace/types.py. */
interface Trace {
  seq_no?: number | null;
  event_type: string;
  step_number?: number | null;
  action_index?: number | null;
  action_type?: string | null;
  action_name?: string | null;
  agent_name?: string | null;
  label?: string | null;
  input?: Record<string, unknown> | null;
  output?: unknown;
  message?: string | null;
  reasoning?: string | null;
  success?: boolean | null;
  error?: string | null;
  duration_ms?: number | null;
  usage?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  surface_op?: unknown;
  source_event_seqs?: number[] | null;
  timestamp?: string;
}

interface Usage { input: number; output: number; cacheRead: number; cacheWrite: number; cost?: number }

/** One action inside a step: the call the assistant made, and what came back. */
interface Call {
  id: string;
  name: string;
  /** "tool" | "skill" — the log's own `action_type`. */
  type: string;
  index?: number;
  input?: Record<string, unknown>;
  /** The result, as derive.py would put it in a ToolMessage — just formatted. */
  result?: string;
  success?: boolean;
  error?: string;
  durationMs?: number;
  /** No `*_start` was logged for this result, so its arguments are unknown. */
  unpaired?: boolean;
}

type Turn =
  | { type: 'task'; id: string; text: string; agent?: string }
  | { type: 'step'; id: string; step: number; reasoning: string; usage?: Usage; durationMs?: number; calls: Call[] }
  | { type: 'result'; id: string; text: string; success: boolean; error?: string; durationMs?: number }
  | { type: 'compaction'; id: string; text: string; shadowed: number[] }
  | { type: 'error'; id: string; text: string };

interface Trajectory {
  turns: Turn[];
  steps: number;
  calls: number;
  failures: number;
  /** Run total, taken from `agent_end` when the log has one — see `readStats`. */
  usage?: Usage;
  wallMs?: number;
  /** undefined while the run is still going: no `agent_end` has been written. */
  outcome?: 'ok' | 'failed';
  /** Set when the surface declarations could not be folded; see `foldSurface`. */
  warning?: string;
}

/** One submitted task and the trace it produced. A conversation holds several. */
interface Run { taskId: string; title: string; at?: string; traces: Trace[] }

/** Steps slower than this are called out — the eleven-minute step is the one
 *  you opened this dialog to find. */
const SLOW_STEP_MS = 60_000;
/** Text longer than this is cut before it reaches the DOM. A single tool result
 *  can be a megabyte of build log, and pasting one into a <pre> costs a layout
 *  pass whether or not anybody scrolls it. The Copy button still copies it. */
const MAX_BLOCK_CHARS = 200_000;

export function TrajectoryDialog({ request, subscribe, sessionId, conversationId, onClose }: {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId: string;
  conversationId?: string;
  onClose: () => void;
}) {
  const [events, setEvents] = useState<GatewayEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [onlyFailures, setOnlyFailures] = useState(false);
  // Collapsed by default, because a run is dozens of steps; `allOpen` is what
  // Expand all flips, and `overrides` is what a single chevron flips on top.
  const [allOpen, setAllOpen] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});

  // The transcript on disk, not the Gateway's in-memory buffer: the buffer is
  // bounded and dies with the process, and every `trace.event` carrying this
  // conversation's id was appended to the transcript as it happened.
  useEffect(() => {
    if (!conversationId) { setLoading(false); return; }
    let cancelled = false;
    void (async () => {
      try {
        const replay = await request('conversation.events', { session_id: sessionId, conversation_id: conversationId });
        if (cancelled) return;
        if (!replay.ok) throw new Error(replay.error?.message ?? 'Could not read this conversation');
        const history = (replay.result as { events?: GatewayEvent[] }).events ?? [];
        setEvents((live) => merge(history, live));
      } catch (cause) {
        if (!cancelled) setFailure(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [request, sessionId, conversationId]);

  // Keep following while it is open, so watching a live run here works as well
  // as opening this on a finished one.
  useEffect(() => subscribe((event) => {
    if (event.session_id !== sessionId) return;
    if (conversationId && event.conversation_id && event.conversation_id !== conversationId) return;
    setEvents((current) => merge(current, [event]));
  }), [subscribe, sessionId, conversationId]);

  const runs = useMemo(() => readRuns(events), [events]);
  const run = runs.find((item) => item.taskId === runId) ?? runs[runs.length - 1];
  const trajectory = useMemo(() => (run ? project(run.traces) : undefined), [run]);

  const isOpen = useCallback((id: string) => overrides[id] ?? allOpen, [overrides, allOpen]);
  const toggle = useCallback((id: string) => setOverrides((current) => ({ ...current, [id]: !(current[id] ?? allOpen) })), [allOpen]);
  const setAll = useCallback((open: boolean) => { setAllOpen(open); setOverrides({}); }, []);
  // Switching runs re-uses ids across a different set of steps.
  useEffect(() => { setOverrides({}); setOnlyFailures(false); }, [run?.taskId]);

  const turns = trajectory?.turns ?? [];
  const shown = onlyFailures ? turns.filter(isFailure) : turns;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      {/* Sized with utilities rather than in science.css: the shadcn content
          sets `w-full max-w-lg p-6 gap-4 grid`, and beating those from a
          stylesheet would depend on when the lazy Science module's CSS
          arrived. Everything that does not conflict lives in science.css. */}
      <DialogContent
        className="trajectory-dialog flex h-[min(880px,calc(100vh-48px))] w-[min(1180px,calc(100vw-40px))] max-w-none flex-col gap-0 overflow-hidden p-0"
      >
        <DialogHeader className="trajectory-head space-y-0 sm:flex-row">
          <div className="trajectory-head-copy">
            <DialogTitle>Trajectory</DialogTitle>
            <DialogDescription>
              {run
                ? <>Every step of this run — reasoning, calls, results, cost and time.</>
                : <>Nothing has run in this conversation yet.</>}
            </DialogDescription>
          </div>
          {runs.length > 1 ? (
            <Select value={run?.taskId} onValueChange={setRunId}>
              <SelectTrigger className="trajectory-run-picker"><SelectValue /></SelectTrigger>
              <SelectContent className="max-h-72">
                {runs.map((item, index) => (
                  <SelectItem key={item.taskId} value={item.taskId} className="text-xs">
                    {`Run ${index + 1} · ${item.title}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
        </DialogHeader>

        {trajectory ? <Summary trajectory={trajectory} /> : null}

        {trajectory ? (
          <div className="trajectory-toolbar">
            <span>{trajectory.steps} step{trajectory.steps === 1 ? '' : 's'} · {trajectory.calls} call{trajectory.calls === 1 ? '' : 's'}</span>
            {trajectory.failures ? (
              <button
                className={`trajectory-chip fail${onlyFailures ? ' on' : ''}`}
                aria-pressed={onlyFailures}
                onClick={() => setOnlyFailures((current) => !current)}
              >
                <AlertTriangle size={11} /> {trajectory.failures} failed
              </button>
            ) : null}
            <span className="trajectory-toolbar-spacer" />
            <button className="trajectory-chip" onClick={() => setAll(true)}><ChevronsUpDown size={11} /> Expand all</button>
            <button className="trajectory-chip" onClick={() => setAll(false)}><ChevronsDownUp size={11} /> Collapse all</button>
          </div>
        ) : null}

        <div className="trajectory-body">
          {loading ? (
            <p className="trajectory-notice"><Loader2 className="science-spinner inline" /> Reading the trace…</p>
          ) : failure ? (
            <p className="trajectory-notice error">{failure}</p>
          ) : !trajectory ? (
            <p className="trajectory-notice">
              This conversation has no trace yet. Ask for something and the run's every step appears here.
            </p>
          ) : (
            <>
              {trajectory.warning ? <p className="trajectory-notice warn">{trajectory.warning}</p> : null}
              {shown.map((turn) => (
                <TurnCard key={turn.id} turn={turn} open={isOpen(turn.id)} onToggle={() => toggle(turn.id)} />
              ))}
              {onlyFailures && !shown.length ? <p className="trajectory-notice">No failed step is on this run's history.</p> : null}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* --- Header -------------------------------------------------------------- */

function Summary({ trajectory }: { trajectory: Trajectory }) {
  const usage = trajectory.usage;
  const prompt = usage ? usage.input + usage.cacheRead : 0;
  // Share of the prompt that was READ from cache rather than paid for. A step
  // that re-read 70k tokens and one that hit the cache look identical without
  // it, which is the whole reason `usage` is a first-class field on the event.
  const cached = usage && prompt ? Math.round((usage.cacheRead / prompt) * 100) : undefined;
  return (
    <div className="trajectory-summary">
      <Stat label="Steps" value={String(trajectory.steps)} hint={`${trajectory.calls} call${trajectory.calls === 1 ? '' : 's'}`} />
      <Stat
        label="Wall time"
        value={trajectory.wallMs === undefined ? '—' : formatDuration(trajectory.wallMs)}
        hint={trajectory.wallMs !== undefined && trajectory.steps ? `${formatDuration(trajectory.wallMs / trajectory.steps)} / step` : undefined}
      />
      <Stat
        label="Tokens in"
        value={usage ? formatTokens(prompt) : '—'}
        hint={usage && usage.cacheWrite ? `${formatTokens(usage.cacheWrite)} written to cache` : undefined}
      />
      <Stat label="Tokens out" value={usage ? formatTokens(usage.output) : '—'} hint={usage?.cost ? `$${usage.cost.toFixed(4)}` : undefined} />
      <div className="trajectory-stat">
        <span>From cache</span>
        <strong>{cached === undefined ? '—' : `${cached}%`}</strong>
        <div className="trajectory-meter"><i style={{ width: `${cached ?? 0}%` }} /></div>
      </div>
      <div className={`trajectory-stat outcome ${trajectory.outcome ?? 'running'}`}>
        <span>Outcome</span>
        <strong>
          {trajectory.outcome === 'ok' ? <><Check size={13} /> Completed</>
            : trajectory.outcome === 'failed' ? <><AlertTriangle size={13} /> Failed</>
              : <><Loader2 className="science-spinner inline" /> Running</>}
        </strong>
        {trajectory.failures ? <em>{trajectory.failures} failed call{trajectory.failures === 1 ? '' : 's'}</em> : null}
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="trajectory-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <em>{hint}</em> : null}
    </div>
  );
}

/* --- Turns --------------------------------------------------------------- */

function TurnCard({ turn, open, onToggle }: { turn: Turn; open: boolean; onToggle: () => void }) {
  if (turn.type === 'task') {
    return (
      <article className="trajectory-turn task">
        <header><strong>Task</strong>{turn.agent ? <em>{turn.agent}</em> : null}</header>
        <p>{turn.text || 'No task text was recorded.'}</p>
      </article>
    );
  }
  if (turn.type === 'result') {
    return (
      <article className={`trajectory-turn result${turn.success ? '' : ' failed'}`}>
        <header>
          <strong>{turn.success ? 'Result' : 'Run failed'}</strong>
          {turn.durationMs !== undefined ? <em>{formatDuration(turn.durationMs)}</em> : null}
        </header>
        {turn.error ? <Block label="Error" text={turn.error} tone="error" /> : null}
        {turn.text ? <p>{turn.text}</p> : null}
      </article>
    );
  }
  if (turn.type === 'compaction') {
    return (
      <article className="trajectory-turn compaction">
        <header>
          <strong>Context compacted</strong>
          <em>{turn.shadowed.length ? `stands for ${turn.shadowed.length} earlier events` : 'summary'}</em>
        </header>
        {/* The summary is what the model now sees in place of the range it
            replaced — derive.py yields it and not the events behind it. The
            originals are still in the log; the seqs are the way back to them. */}
        <p>{turn.text}</p>
        {turn.shadowed.length ? <p className="trajectory-shadowed">Shadowed events: {turn.shadowed.join(', ')}</p> : null}
      </article>
    );
  }
  if (turn.type === 'error') {
    return (
      <article className="trajectory-turn result failed">
        <header><strong>Error</strong></header>
        <Block label="Error" text={turn.text} tone="error" />
      </article>
    );
  }

  const failed = turn.calls.filter((call) => call.success === false).length;
  const slow = turn.durationMs !== undefined && turn.durationMs >= SLOW_STEP_MS;
  const preview = firstLine(turn.reasoning) || turn.calls.map((call) => call.name).join(', ') || 'No reasoning was recorded';
  return (
    <article className={`trajectory-step${failed ? ' failed' : ''}${open ? ' open' : ''}`}>
      <button className="trajectory-step-head" onClick={onToggle} aria-expanded={open}>
        {open ? <ChevronDown size={13} className="trajectory-caret" /> : <ChevronRight size={13} className="trajectory-caret" />}
        <span className="trajectory-step-badge">{turn.step}</span>
        <span className="trajectory-step-line">{preview}</span>
        <span className="trajectory-step-meta">
          {turn.calls.length ? <em><Wrench size={10} /> {turn.calls.length}</em> : null}
          {turn.usage ? (
            <em className="tokens" title={`${turn.usage.input} in · ${turn.usage.cacheRead} from cache · ${turn.usage.output} out`}>
              {formatTokens(turn.usage.input + turn.usage.cacheRead)}{' → '}{formatTokens(turn.usage.output)}
              {/* What the prompt cost is the difference between a step that
                  re-read 70k tokens and one that did not pay for them. */}
              {turn.usage.cacheRead ? <i>{Math.round((turn.usage.cacheRead / (turn.usage.input + turn.usage.cacheRead)) * 100)}% cached</i> : null}
            </em>
          ) : null}
          {turn.durationMs !== undefined ? <em className={slow ? 'slow' : ''}>{formatDuration(turn.durationMs)}</em> : null}
          {failed ? <em className="fail"><AlertTriangle size={10} /> {failed}</em> : null}
        </span>
      </button>
      {open ? (
        <div className="trajectory-step-body">
          {turn.reasoning ? <div className="trajectory-reasoning">{turn.reasoning}</div> : null}
          {turn.calls.map((call) => <CallCard key={call.id} call={call} />)}
          {!turn.reasoning && !turn.calls.length ? <p className="trajectory-empty">This step recorded neither reasoning nor a call.</p> : null}
        </div>
      ) : null}
    </article>
  );
}

function CallCard({ call }: { call: Call }) {
  const command = call.input && stringOf(call.input.command ?? call.input.cmd ?? call.input.script ?? call.input.code);
  const rest = call.input
    ? Object.fromEntries(Object.entries(call.input).filter(([key]) => !['command', 'cmd', 'script', 'code'].includes(key)))
    : {};
  return (
    <section className={`trajectory-call${call.success === false ? ' failed' : ''}`}>
      <header>
        <span className="trajectory-call-icon">{call.type === 'skill' ? <Layers size={12} /> : <Terminal size={12} />}</span>
        <strong>{call.name || 'call'}</strong>
        <span className="trajectory-call-args">{command ? firstLine(command) : summarise(rest)}</span>
        {call.durationMs !== undefined ? <em>{formatDuration(call.durationMs)}</em> : null}
        <span className={`trajectory-pill ${call.success === false ? 'fail' : call.success ? 'ok' : ''}`}>
          {call.success === false ? 'failed' : call.success ? 'ok' : 'no result'}
        </span>
      </header>
      {command ? <Block label="Command" text={command} tone="command" /> : null}
      {Object.keys(rest).length ? <Block label="Arguments" text={JSON.stringify(rest, null, 2)} /> : null}
      {call.unpaired ? <p className="trajectory-empty">No call was logged for this result, so its arguments are unknown.</p> : null}
      {call.error ? <Block label="Error" text={call.error} tone="error" /> : null}
      {call.result ? <Block label="Result" text={call.result} tone="output" /> : null}
    </section>
  );
}

/** A block of logged text: clamped, expandable, scrolling inside its own box.
 *
 * Never allowed to set the dialog's height — a single 4000-line result would
 * otherwise push the summary header off the top of a 40-step run. */
function Block({ label, text, tone = 'data' }: { label: string; text: string; tone?: 'data' | 'command' | 'output' | 'error' }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const lines = useMemo(() => countLines(text), [text]);
  const long = lines > 12 || text.length > 900;
  const body = text.length > MAX_BLOCK_CHARS
    ? `${text.slice(0, MAX_BLOCK_CHARS)}\n… ${formatCount(text.length - MAX_BLOCK_CHARS)} more characters (Copy takes all of it)`
    : text;
  return (
    <div className={`trajectory-block ${tone}${expanded ? ' expanded' : ''}`}>
      <header>
        <strong>{label}</strong>
        <em>{lines} line{lines === 1 ? '' : 's'} · {formatBytes(text.length)}</em>
        <button onClick={() => { void navigator.clipboard?.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1200); }}>
          {copied ? <Check size={11} /> : <Copy size={11} />} {copied ? 'Copied' : 'Copy'}
        </button>
      </header>
      <pre><code>{body}</code></pre>
      {long ? (
        <button className="trajectory-block-more" onClick={() => setExpanded((current) => !current)}>
          {expanded ? 'Show less' : `Show all ${lines} lines`}
        </button>
      ) : null}
    </div>
  );
}

/* --- Reading the log ------------------------------------------------------ */

/** Gateway events, de-duplicated by seq_no and back in write order.
 *
 * The transcript read on open and the socket both deliver the same events, and
 * a live one can land before the read returns. seq_no is per-conversation and
 * assigned once, so it is what tells the two copies apart. */
function merge(base: GatewayEvent[], extra: GatewayEvent[]): GatewayEvent[] {
  const seen = new Map<number, GatewayEvent>();
  for (const event of [...base, ...extra]) seen.set(event.seq_no, event);
  return [...seen.values()].sort((left, right) => left.seq_no - right.seq_no);
}

/** The conversation's events, split into the runs that produced them.
 *
 * One conversation holds several submissions; each is its own trajectory, and
 * showing forty steps from three different questions as one list would be a
 * trajectory of nothing. Grouped by task_id, the id every trace carries. */
function readRuns(events: GatewayEvent[]): Run[] {
  const runs = new Map<string, Run>();
  const at = (taskId: string) => {
    const existing = runs.get(taskId);
    if (existing) return existing;
    const created: Run = { taskId, title: 'Untitled run', traces: [] };
    runs.set(taskId, created);
    return created;
  };
  for (const event of events) {
    if (!event.task_id) continue;
    if (event.type === 'task.submitted') {
      const run = at(event.task_id);
      run.title = truncate(String(event.payload?.content ?? '').split('\n')[0] ?? '', 64) || run.title;
      run.at = event.timestamp;
      continue;
    }
    if (event.type !== 'trace.event') continue;
    at(event.task_id).traces.push(event.payload as unknown as Trace);
  }
  // Runs with no trace at all (submitted, never started) have nothing to show.
  const listed = [...runs.values()].filter((run) => run.traces.length);
  for (const run of listed) {
    if (run.title !== 'Untitled run') continue;
    const start = run.traces.find((trace) => trace.event_type === 'agent_start');
    run.title = truncate(String((start?.input as Record<string, unknown> | undefined)?.task ?? '').split('\n')[0] ?? '', 64) || run.title;
  }
  return listed;
}

/** Replay the surface declarations — the TS half of `trace/surface.py`.
 *
 * `append` joins at the tail; `{op: "replace", start, end}` stands in place of
 * an inclusive range of it. Returns the seqs that still stand, plus what each
 * replacement shadowed, or `undefined` when the declarations do not fold —
 * which is what makes the caller fall back to reading the log in write order.
 *
 * The one rule not enforced here is surface.py's citation check: a replacement
 * that fails to cite what it shadows makes the originals unrecoverable, which
 * is a reason for the backend to refuse to build a history, but not a reason
 * for a viewer to refuse to draw one — the folded order is identical either way. */
function foldSurface(traces: Trace[]): { nodes: number[]; shadowed: Map<number, number[]> } | undefined {
  const nodes: number[] = [];
  const shadowed = new Map<number, number[]>();
  for (const trace of traces) {
    const op = trace.surface_op;
    if (op === null || op === undefined) continue;      // log-only; never joined
    const seq = trace.seq_no;
    if (typeof seq !== 'number') return undefined;      // cannot be placed
    if (op === 'append') { nodes.push(seq); continue; }
    if (typeof op !== 'object' || (op as Record<string, unknown>).op !== 'replace') return undefined;
    const span = op as { start?: unknown; end?: unknown };
    const from = nodes.indexOf(Number(span.start));
    const to = nodes.indexOf(Number(span.end));
    if (from < 0 || to < 0 || from > to) return undefined;
    shadowed.set(seq, nodes.slice(from, to + 1));
    nodes.splice(from, to - from + 1, seq);
  }
  return { nodes, shadowed };
}

/** One run's log as turns — the projection `derive.py` performs, rendered.
 *
 * `[user, assistant(+tool_calls), tool…, assistant]`: `agent_start` is the
 * task, `agent_call` is the step's assistant turn carrying the calls that
 * step's `*_start` events recorded, and the `*_call` results that follow belong
 * to the turn just placed — the log writes a step's tool events before its
 * `agent_call`, so results are flushed after the step, never before it. */
function project(traces: Trace[]): Trajectory {
  const folded = foldSurface(traces);
  // Older logs predate `surface_op` and would fold to nothing; reading them in
  // write order is strictly better than showing an empty dialog.
  const usable = folded && folded.nodes.length ? folded : undefined;
  const onSurface = usable ? new Set(usable.nodes) : undefined;
  const warning = folded ? undefined
    : 'This log\'s surface declarations do not fold, so every event is shown in write order. What the agent actually saw may be shorter.';

  const callsByStep = new Map<number, Trace[]>();
  for (const trace of traces) {
    if (trace.event_type !== 'tool_start' && trace.event_type !== 'skill_start') continue;
    const step = trace.step_number ?? 0;
    callsByStep.set(step, [...(callsByStep.get(step) ?? []), trace]);
  }

  const turns: Turn[] = [];
  let pending: Trace[] = [];
  let current: (Turn & { type: 'step' }) | undefined;

  const flush = () => {
    for (const result of pending) {
      // A result with no step turn before it (an interrupted run, a log written
      // without `agent_call`) still happened; give it a step of its own rather
      // than dropping it.
      if (!current || current.step !== (result.step_number ?? current.step)) {
        current = { type: 'step', id: `orphan-${result.seq_no ?? turns.length}`, step: result.step_number ?? 0, reasoning: '', calls: [] };
        turns.push(current);
      }
      const id = callId(result);
      const target = current.calls.find((call) => call.id === id);
      if (target) {
        target.result = resultText(result);
        target.success = result.success ?? undefined;
        target.error = result.error ?? undefined;
        target.durationMs = numberOf(result.duration_ms);
      } else {
        current.calls.push({
          id, name: result.action_name ?? '', type: result.action_type ?? 'tool',
          index: numberOf(result.action_index), result: resultText(result),
          success: result.success ?? undefined, error: result.error ?? undefined,
          durationMs: numberOf(result.duration_ms), unpaired: true,
        });
      }
    }
    pending = [];
  };

  let usage: Usage | undefined;
  let summed: Usage | undefined;
  let wallMs: number | undefined;
  let outcome: Trajectory['outcome'];

  for (const trace of traces) {
    if (onSurface && (typeof trace.seq_no !== 'number' || !onSurface.has(trace.seq_no))) continue;
    const type = trace.event_type;
    if (type === 'agent_start') {
      flush();
      current = undefined;
      turns.push({ type: 'task', id: `task-${trace.seq_no ?? turns.length}`, text: String((trace.input ?? {}).task ?? ''), agent: trace.agent_name ?? undefined });
    } else if (type === 'agent_call') {
      const step = trace.step_number ?? 0;
      const stepUsage = readUsage(trace.usage);
      current = {
        type: 'step', id: `step-${trace.seq_no ?? step}`, step,
        reasoning: reasoningOf(trace),
        usage: stepUsage,
        durationMs: numberOf(trace.duration_ms),
        calls: (callsByStep.get(step) ?? []).map((start) => ({
          id: callId(start),
          name: start.action_name ?? '',
          type: start.action_type ?? 'tool',
          index: numberOf(start.action_index),
          input: start.input ?? undefined,
        })),
      };
      turns.push(current);
      flush();
      if (stepUsage) summed = addUsage(summed, stepUsage);
    } else if (type === 'tool_call' || type === 'skill_call') {
      pending.push(trace);
    } else if (type === 'agent_end') {
      flush();
      outcome = trace.success === false ? 'failed' : 'ok';
      wallMs = numberOf(trace.duration_ms);
      // The run total the hook accumulated across every step. Preferred over
      // the sum because it is the figure the backend itself kept; summing the
      // steps AND adding this would count every token twice.
      usage = readUsage(trace.usage) ?? usage;
      turns.push({
        type: 'result', id: `end-${trace.seq_no ?? turns.length}`,
        text: textOf(trace.output ?? trace.message), success: trace.success !== false,
        error: trace.error ?? undefined, durationMs: wallMs,
      });
      current = undefined;
    } else if (type === 'custom' && (trace.metadata ?? {}).type === 'compaction') {
      flush();
      turns.push({
        type: 'compaction', id: `fold-${trace.seq_no ?? turns.length}`,
        text: textOf(trace.message),
        shadowed: (typeof trace.seq_no === 'number' && usable?.shadowed.get(trace.seq_no)) || trace.source_event_seqs || [],
      });
    } else if (type === 'error') {
      flush();
      turns.push({ type: 'error', id: `err-${trace.seq_no ?? turns.length}`, text: textOf(trace.error ?? trace.message) });
    }
  }
  flush();

  const steps = turns.filter((turn) => turn.type === 'step').length;
  const calls = turns.reduce((total, turn) => total + (turn.type === 'step' ? turn.calls.length : 0), 0);
  const failures = turns.reduce((total, turn) => total
    + (turn.type === 'step' ? turn.calls.filter((call) => call.success === false).length : 0)
    + (turn.type === 'error' ? 1 : 0), 0);
  if (wallMs === undefined) wallMs = spanOf(traces);
  return { turns, steps, calls, failures, usage: usage ?? summed, wallMs, outcome, warning };
}

/** The model's id for a call, falling back to position — `derive.py::_call_id`.
 *  Logs written before `call_id` existed have only (step, index) to pair on. */
function callId(trace: Trace): string {
  const explicit = (trace.metadata ?? {}).call_id;
  if (explicit) return String(explicit);
  return `step${trace.step_number ?? 0}_call${trace.action_index ?? 0}`;
}

/** What derive.py puts in the ToolMessage, formatted for reading rather than
 *  for a provider: `output` is the result the log stored, `message` its `str()`. */
function resultText(trace: Trace): string {
  if (typeof trace.output === 'string') return trace.output;
  if (trace.output !== null && trace.output !== undefined) return JSON.stringify(trace.output, null, 2);
  return textOf(trace.message);
}

/** The step's reasoning, including the shape older logs wrote it in. */
function reasoningOf(trace: Trace): string {
  if (typeof trace.reasoning === 'string' && trace.reasoning) return trace.reasoning;
  if (typeof trace.message !== 'string') return '';
  const legacy = trace.message.match(/^\{'reasoning':\s*(?:None|'([\s\S]*)')\}$/);
  if (!legacy) return trace.message;
  return legacy[1]?.replaceAll('\\n', '\n').replaceAll("\\'", "'") ?? '';
}

function readUsage(raw: Record<string, unknown> | null | undefined): Usage | undefined {
  if (!raw) return undefined;
  const usage: Usage = {
    input: numberOf(raw.input_tokens) ?? 0,
    output: numberOf(raw.output_tokens) ?? 0,
    cacheRead: numberOf(raw.cache_read_tokens) ?? 0,
    cacheWrite: numberOf(raw.cache_write_tokens) ?? 0,
    cost: numberOf(raw.cost),
  };
  return usage.input || usage.output || usage.cacheRead || usage.cacheWrite ? usage : undefined;
}

function addUsage(total: Usage | undefined, one: Usage): Usage {
  if (!total) return { ...one };
  return {
    input: total.input + one.input,
    output: total.output + one.output,
    cacheRead: total.cacheRead + one.cacheRead,
    cacheWrite: total.cacheWrite + one.cacheWrite,
    cost: one.cost === undefined ? total.cost : (total.cost ?? 0) + one.cost,
  };
}

/** Wall time from the timestamps, for a run whose `agent_end` never arrived. */
function spanOf(traces: Trace[]): number | undefined {
  const times = traces.map((trace) => Date.parse(trace.timestamp ?? '')).filter((value) => !Number.isNaN(value));
  if (times.length < 2) return undefined;
  return Math.max(...times) - Math.min(...times);
}

function isFailure(turn: Turn): boolean {
  if (turn.type === 'error') return true;
  if (turn.type === 'result') return !turn.success;
  return turn.type === 'step' && turn.calls.some((call) => call.success === false);
}

/* --- Formatting ----------------------------------------------------------- */

function numberOf(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function stringOf(value: unknown): string | undefined {
  if (typeof value === 'string') return value || undefined;
  return value === null || value === undefined ? undefined : JSON.stringify(value, null, 2);
}

function textOf(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function firstLine(text: string): string {
  const line = text.split('\n').find((candidate) => candidate.trim());
  return truncate((line ?? '').trim(), 160);
}

function summarise(input: Record<string, unknown>): string {
  const entries = Object.entries(input);
  if (!entries.length) return '';
  return truncate(entries.map(([key, value]) => `${key}=${firstLine(textOf(value))}`).join(' '), 160);
}

function truncate(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function countLines(text: string): number {
  let lines = 1;
  for (let index = 0; index < text.length; index += 1) if (text[index] === '\n') lines += 1;
  return lines;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function formatTokens(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(2)}M`;
}

function formatCount(value: number): string {
  return value.toLocaleString();
}

function formatBytes(chars: number): string {
  if (chars < 1024) return `${chars} B`;
  if (chars < 1024 * 1024) return `${(chars / 1024).toFixed(1)} kB`;
  return `${(chars / (1024 * 1024)).toFixed(1)} MB`;
}
