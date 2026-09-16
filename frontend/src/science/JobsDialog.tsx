import { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, CircleDot, Clock, Loader2, Terminal } from 'lucide-react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import type { RequestFn } from '../canvas/types';
import type { GatewayEvent } from '../controllers/gateway';

/* ===========================================================================
   Background jobs: the work that outlives the turn that started it.

   Not a transcript entry, because it is not something that HAPPENED — it is
   something that is still happening. A line in the thread saying "started a
   job" scrolls away while the job is still running, which is precisely
   backwards. So it lives in the thread bar, as a count that is true now.

   The registry read here is the same one the agent's own `job_list_tool`
   reads (gateway `job.list`). Two sources for "what is outstanding" would
   disagree the moment one lagged, and that moment is exactly when someone is
   deciding whether it is safe to close the tab.
   =========================================================================== */

/** One job as `_command_job_list` returns it — see gateway/service.py. */
export interface Job {
  id: string;
  type: string;
  label: string;
  status: string;
  running: boolean;
  exit_code: number | null;
  error: string | null;
  /** Seconds since it started; frozen at the end for a finished job. */
  elapsed: number;
  /** Epoch seconds a reminder next comes due; null for ordinary work. */
  due_at: number | null;
  summary: string;
}

/** While the dialog is open. Everything here is seconds-scale, and the only
 *  number that moves on its own is elapsed. */
const POLL_MS = 3000;
/** A run emits trace events continuously; refreshing on every one would be a
 *  poll wearing a costume. This is the floor between two trace-driven reads. */
const TRACE_REFRESH_MS = 6000;

/** The job registry, kept current without polling in the background.
 *
 * Nothing on the wire announces a job starting or finishing, so the refresh is
 * pinned to the moments when one plausibly did: the task lifecycle, and — at
 * most once every few seconds — the trace of a run in progress. The steady
 * interval only turns on while the dialog is open, where the numbers are being
 * read; a closed dialog costs one request per run boundary.
 *
 * Every failure degrades to an empty list. A gateway that cannot answer
 * `job.list` must cost the conversation nothing at all. */
export function useJobs({ request, subscribe, sessionId, polling }: {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId: string;
  polling: boolean;
}): { jobs: Job[]; running: number; reload: () => void } {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [running, setRunning] = useState(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const reload = useCallback(() => {
    void (async () => {
      try {
        const response = await request('job.list', { session_id: sessionId });
        if (!alive.current) return;
        if (!response.ok) { setJobs([]); setRunning(0); return; }
        const result = (response.result ?? {}) as { jobs?: Job[]; running?: number };
        setJobs(Array.isArray(result.jobs) ? result.jobs : []);
        setRunning(typeof result.running === 'number' ? result.running : 0);
      } catch {
        if (alive.current) { setJobs([]); setRunning(0); }
      }
    })();
  }, [request, sessionId]);

  useEffect(() => { reload(); }, [reload]);

  const lastTrace = useRef(0);
  useEffect(() => subscribe((event) => {
    if (event.session_id !== sessionId) return;
    if (event.type === 'task.submitted' || event.type === 'task.started'
      || event.type === 'task.completed' || event.type === 'task.failed'
      || event.type === 'task.cancelled') { reload(); return; }
    // A job is born inside a step, so the trace is the only hint that one
    // appeared mid-run. Rate-limited, because a step emits several events.
    if (event.type !== 'trace.event') return;
    const now = Date.now();
    if (now - lastTrace.current < TRACE_REFRESH_MS) return;
    lastTrace.current = now;
    reload();
  }), [subscribe, sessionId, reload]);

  useEffect(() => {
    if (!polling) return undefined;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, reload]);

  return { jobs, running, reload };
}

/** The registry, newest first, with one job's output beside it.
 *
 * Structure and stylesheet are the Trajectory dialog's — same shell, same
 * header, same block of logged text. A second dialog idiom in one column would
 * make the two read as unrelated features rather than two views of one run. */
export function JobsDialog({ request, sessionId, jobs, onClose }: {
  request: RequestFn;
  sessionId: string;
  jobs: Job[];
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<string>();
  const [output, setOutput] = useState<{ text: string; truncated: boolean }>();
  const [reading, setReading] = useState(false);
  const [unreadable, setUnreadable] = useState(false);

  // The first job is the newest and almost always the one being asked about.
  const chosen = jobs.find((job) => job.id === selected) ?? jobs[0];
  const chosenId = chosen?.id;

  // Re-runs whenever `jobs` is replaced, which is every poll tick — so a
  // running job's output grows in place while it is being watched. Only a
  // change of job shows a spinner; a refresh of the same job must not blink.
  const shownFor = useRef<string>();
  useEffect(() => {
    if (!chosenId) { setOutput(undefined); return undefined; }
    let cancelled = false;
    if (shownFor.current !== chosenId) {
      shownFor.current = chosenId;
      setOutput(undefined);
      setUnreadable(false);
      setReading(true);
    }
    void (async () => {
      try {
        const response = await request('job.output', { session_id: sessionId, job_id: chosenId });
        if (cancelled) return;
        if (!response.ok) { setUnreadable(true); setOutput(undefined); return; }
        const result = (response.result ?? {}) as { output?: string; truncated?: boolean };
        setUnreadable(false);
        setOutput({ text: String(result.output ?? ''), truncated: Boolean(result.truncated) });
      } catch {
        if (!cancelled) { setUnreadable(true); setOutput(undefined); }
      } finally {
        if (!cancelled) setReading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [request, sessionId, chosenId, jobs]);

  const running = jobs.filter((job) => job.running).length;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      {/* Sized with utilities for the same reason the Trajectory dialog is:
          the shadcn content sets `max-w-lg p-6 gap-4 grid`, and beating that
          from a lazily-loaded stylesheet is a race. */}
      <DialogContent
        className="jobs-dialog flex h-[min(700px,calc(100vh-48px))] w-[min(1020px,calc(100vw-40px))] max-w-none flex-col gap-0 overflow-hidden p-0"
      >
        <DialogHeader className="trajectory-head space-y-0 sm:flex-row">
          <div className="trajectory-head-copy">
            <DialogTitle>Background jobs</DialogTitle>
            <DialogDescription>
              {jobs.length
                ? <>{running} running of {jobs.length} this project has started. Elapsed is what separates working from hung.</>
                : <>Nothing has been started in the background.</>}
            </DialogDescription>
          </div>
        </DialogHeader>

        {jobs.length ? (
          <div className="jobs-layout">
            <ol className="jobs-list">
              {jobs.map((job) => (
                <li key={job.id}>
                  <button
                    className={`jobs-row${job.id === chosenId ? ' on' : ''}${job.running ? ' live' : ''}`}
                    onClick={() => setSelected(job.id)}
                  >
                    <span className="jobs-row-icon"><JobIcon job={job} /></span>
                    <span className="jobs-row-main">
                      <strong>{job.label || job.id}</strong>
                      <em>{job.type} · {job.id}</em>
                    </span>
                    <span className="jobs-row-meta">
                      <span className={`trajectory-pill ${toneOf(job)}`}>{stateOf(job)}</span>
                      <em>{timingOf(job)}</em>
                    </span>
                  </button>
                </li>
              ))}
            </ol>

            <div className="jobs-output">
              {chosen ? (
                <>
                  <header>
                    <strong>{chosen.label || chosen.id}</strong>
                    {/* Composed rather than `job.summary`, which repeats the
                        label the line above already gives in full. */}
                    <em>{chosen.type} · {chosen.id} · {stateOf(chosen)} · {timingOf(chosen)}</em>
                  </header>
                  {chosen.error ? <p className="jobs-error">{chosen.error}</p> : null}
                  {output?.truncated ? (
                    <p className="jobs-trimmed">Output passed its cap; the head was dropped, so this starts mid-run.</p>
                  ) : null}
                  {reading && output === undefined ? (
                    <p className="trajectory-notice"><Loader2 className="science-spinner inline" /> Reading output…</p>
                  ) : unreadable ? (
                    <p className="trajectory-notice">This job's output could not be read.</p>
                  ) : output?.text ? (
                    <pre>{output.text}</pre>
                  ) : (
                    <p className="trajectory-notice">
                      {chosen.running ? 'Nothing printed yet.' : 'This job printed nothing.'}
                    </p>
                  )}
                </>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="trajectory-body">
            <p className="trajectory-notice">
              Nothing is running. Work the agent puts in the background — a long build, a server,
              a reminder — appears here while it lasts, and stays afterwards with what it printed.
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function JobIcon({ job }: { job: Job }) {
  if (job.due_at !== null && job.due_at !== undefined) return <Clock size={12} />;
  if (job.type === 'agent') return <Bot size={12} />;
  if (job.type === 'bash' || job.type === 'terminal') return <Terminal size={12} />;
  return <CircleDot size={12} />;
}

/** The state as a person reads it — the exit code included, because "exited"
 *  alone does not say whether the thing worked. */
function stateOf(job: Job): string {
  if (job.status === 'exited' && job.exit_code !== null && job.exit_code !== undefined) {
    return job.exit_code === 0 ? 'exited 0' : `exited ${job.exit_code}`;
  }
  if (job.status === 'scheduled' && job.due_at) {
    return job.due_at - Date.now() / 1000 > 0 ? 'scheduled' : 'due now';
  }
  return job.status;
}

function toneOf(job: Job): string {
  if (job.status === 'failed') return 'fail';
  if (job.status === 'killed') return 'warn';
  if (job.status === 'exited') return job.exit_code ? 'fail' : 'ok';
  return 'run';
}

/** Elapsed for work, time-to-due for a reminder. A reminder that has not begun
 *  showing "0.0s elapsed" is worse than no number at all. */
function timingOf(job: Job): string {
  if (job.status === 'scheduled' && job.due_at) {
    const remaining = job.due_at - Date.now() / 1000;
    return remaining > 0 ? `in ${formatSeconds(remaining)}` : `${formatSeconds(-remaining)} late`;
  }
  return formatSeconds(job.elapsed);
}

export function formatSeconds(seconds: number): string {
  const value = Math.max(0, seconds);
  if (value < 60) return `${value < 10 ? value.toFixed(1) : Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(value % 60)}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}
