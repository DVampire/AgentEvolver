import { useCallback, useEffect, useRef, useState } from 'react';
import { Cpu, FlaskConical, HardDrive, Loader2, MemoryStick, Plus, RefreshCw, SquareArrowOutUpRight, Zap } from 'lucide-react';

import { Button } from '../components/ui/button';
import type { RequestFn } from '../canvas/types';
// Owned here rather than in canvas.css: both are lazy modules, so parking these
// styles there would leave this view unstyled until the canvas had been opened.
import '../style/science.css';

/** Keep-alive cadence. The manager also refreshes the idle clock on every
 *  proxied request, so this only matters while the Lab sits untouched. */
const HEARTBEAT_MS = 60_000;
/** How often the Compute panel re-reads the workstation. Slow on purpose: each
 *  poll is a `docker exec` into the container, and GPU memory over a training
 *  run is a curve, not something you watch tick. */
const COMPUTE_MS = 15_000;

interface ScienceStatus { running: boolean; path?: string; gpus?: string }
interface Gpu { index: number; name: string; memory_used_mb: number; memory_total_mb: number; utilization_percent: number }
interface Compute {
  running: boolean; gpus: Gpu[];
  cpu_count?: number | null; memory_total_mb?: number | null; memory_used_mb?: number | null;
  disk_free_mb?: number | null; uptime_seconds: number;
}
interface NotebookEntry { path: string; title: string; size_bytes: number; modified_at: string; cell_count: number }

/** The Science workstation: JupyterLab for this project, with what it runs on.
 *
 * The Lab is served under a path on THIS origin (`/science/<session>/`), so it
 * is reachable at whatever address the browser reached the UI at — a tunnel, a
 * reverse proxy, or plain localhost. JupyterLab is started with that path as
 * `--ServerApp.base_url` so its absolute asset URLs match; see
 * agentevolver/science/README.md. The container starts lazily on first open and
 * is reaped once idle, so mounting this view is what boots it. */
export function ScienceView({ request, sessionId, connected, status, statusText, onOpenNav }: {
  request: RequestFn;
  sessionId?: string;
  connected: boolean;
  status?: string;
  statusText?: string;
  onOpenNav?: () => void;
}) {
  const [path, setPath] = useState<string>();
  const [error, setError] = useState<string>();
  const [starting, setStarting] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [compute, setCompute] = useState<Compute>();
  const [notebooks, setNotebooks] = useState<NotebookEntry[]>([]);

  const start = useCallback(async () => {
    if (!sessionId || !connected) return;
    setStarting(true);
    setError(undefined);
    try {
      const response = await request('science.start', { session_id: sessionId });
      if (!response.ok) throw new Error(response.error?.message ?? 'Could not start the workstation');
      const result = response.result as unknown as ScienceStatus;
      if (!result.path) throw new Error('The gateway did not return a workstation address');
      setPath(result.path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStarting(false);
    }
  }, [request, sessionId, connected]);

  const loadNotebooks = useCallback(async () => {
    if (!sessionId || !connected) return;
    const response = await request('science.notebooks', { session_id: sessionId });
    if (response.ok) setNotebooks((response.result as { notebooks?: NotebookEntry[] }).notebooks ?? []);
  }, [request, sessionId, connected]);

  // Notebooks are workspace files, so they can be listed before the container
  // exists — the list is up the moment the view opens, not after the boot.
  useEffect(() => { void loadNotebooks(); }, [loadNotebooks]);
  useEffect(() => { setPath(undefined); void start(); }, [start]);

  const pathRef = useRef<string | undefined>(undefined);
  pathRef.current = path;
  useEffect(() => {
    if (!sessionId || !connected) return;
    const beat = window.setInterval(() => {
      if (pathRef.current) void request('science.status', { session_id: sessionId });
    }, HEARTBEAT_MS);
    return () => window.clearInterval(beat);
  }, [request, sessionId, connected]);

  useEffect(() => {
    if (!sessionId || !connected || !path) return;
    let cancelled = false;
    const poll = async () => {
      const response = await request('science.compute', { session_id: sessionId });
      if (!cancelled && response.ok) setCompute(response.result as unknown as Compute);
    };
    void poll();
    const timer = window.setInterval(() => void poll(), COMPUTE_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [request, sessionId, connected, path]);

  const createNotebook = async () => {
    if (!sessionId) return;
    const response = await request('science.notebook.create', { session_id: sessionId, name: 'untitled' });
    if (response.ok) {
      await loadNotebooks();
      const created = (response.result as { notebook?: NotebookEntry }).notebook;
      if (created && path) window.open(`${path}/lab/tree/${created.path}`, '_blank', 'noopener');
    }
  };

  if (!sessionId) return <ScienceNotice title="No project" detail="Open or create a project to use the workstation." />;
  if (error) {
    return (
      <ScienceNotice title="The workstation could not start" detail={error}>
        <Button size="md" className="font-normal" onClick={() => void start()}>Try again</Button>
      </ScienceNotice>
    );
  }
  if (!path || starting) {
    return (
      <ScienceNotice
        title="Starting the workstation…"
        detail="Booting this project's JupyterLab container. The first launch also builds the image — CUDA PyTorch, the scientific stack and LaTeX — which takes a while; later projects reuse it."
        spinning
      />
    );
  }

  return (
    <div className="science-view">
      <header className="science-toolbar">
        {onOpenNav ? <button className="mobile-menu" onClick={onOpenNav} aria-label="Open navigation">☰</button> : null}
        <FlaskConical size={14} strokeWidth={1.9} />
        <strong>Science</strong>
        <code className="science-origin" title="This project's workstation path">{path}</code>
        <span className="science-toolbar-spacer" />
        {statusText ? <span className="science-status"><span className={`connection-dot ${status ?? ''}`} />{statusText}</span> : null}
        <Button variant="ghost" size="sm" className="font-normal" onClick={() => window.open(`${path}/lab`, '_blank', 'noopener')}>
          <SquareArrowOutUpRight /> JupyterLab
        </Button>
        <Button variant="ghost" size="sm" className="font-normal" onClick={() => setReloadKey((key) => key + 1)}>
          <RefreshCw /> Reload
        </Button>
      </header>
      <div className="science-body">
        <iframe
          key={reloadKey}
          className="science-frame"
          title="JupyterLab"
          src={`${path}/lab`}
          // No sandbox attribute: the Lab needs same-origin scripting for its
          // kernel WebSocket and service worker, and this frame is our own
          // trusted container.
          allow="clipboard-read; clipboard-write"
        />
        <aside className="science-rail">
          <ComputePanel compute={compute} />
          <NotebookPanel notebooks={notebooks} onCreate={() => void createNotebook()}
                         onOpen={(item) => window.open(`${path}/lab/tree/${item.path}`, '_blank', 'noopener')}
                         onRefresh={() => void loadNotebooks()} />
        </aside>
      </div>
    </div>
  );
}

/** What the workstation is running on, read from inside the container. */
function ComputePanel({ compute }: { compute?: Compute }) {
  const memoryPercent = compute?.memory_total_mb && compute?.memory_used_mb
    ? Math.round((compute.memory_used_mb / compute.memory_total_mb) * 100) : undefined;
  return (
    <section className="science-panel">
      <p className="eyebrow">Compute</p>
      {!compute ? <p className="empty">Reading the workstation…</p> : (
        <>
          {compute.gpus.length ? compute.gpus.map((gpu) => (
            <div className="compute-gpu" key={gpu.index}>
              <div className="compute-gpu-head"><Zap size={13} strokeWidth={1.9} /><strong>{gpu.name}</strong><em>#{gpu.index}</em></div>
              <Meter label="Memory" used={gpu.memory_used_mb} total={gpu.memory_total_mb} unit="MB" />
              <Meter label="Utilisation" used={gpu.utilization_percent} total={100} unit="%" />
            </div>
          )) : (
            // Not an error: the manager starts a CPU-only workstation rather
            // than none at all when the host has no nvidia runtime.
            <p className="empty">No GPUs attached to this workstation.</p>
          )}
          <div className="compute-row"><Cpu size={13} strokeWidth={1.9} /><span>CPU</span><em>{compute.cpu_count ?? '—'} cores</em></div>
          <div className="compute-row"><MemoryStick size={13} strokeWidth={1.9} /><span>Memory</span>
            <em>{memoryPercent === undefined ? '—' : `${gib(compute.memory_used_mb)} / ${gib(compute.memory_total_mb)} GiB`}</em></div>
          <div className="compute-row"><HardDrive size={13} strokeWidth={1.9} /><span>Disk free</span><em>{gib(compute.disk_free_mb)} GiB</em></div>
          <div className="compute-row"><span className="compute-uptime">Up {duration(compute.uptime_seconds)}</span></div>
        </>
      )}
    </section>
  );
}

function NotebookPanel({ notebooks, onCreate, onOpen, onRefresh }: {
  notebooks: NotebookEntry[]; onCreate: () => void;
  onOpen: (item: NotebookEntry) => void; onRefresh: () => void;
}) {
  return (
    <section className="science-panel notebook-panel">
      <p className="eyebrow">
        Notebooks
        <button className="section-refresh" onClick={onRefresh} title="Refresh">↻</button>
      </p>
      <Button variant="ghost" size="sm" className="font-normal notebook-new" onClick={onCreate}>
        <Plus /> New notebook
      </Button>
      {notebooks.length ? notebooks.map((item) => (
        <button className="notebook-row" key={item.path} onClick={() => onOpen(item)} title={item.path}>
          <strong>{item.title}</strong>
          <em>{item.cell_count} cell{item.cell_count === 1 ? '' : 's'}</em>
        </button>
      )) : <p className="empty">No notebooks in this project's workspace yet.</p>}
    </section>
  );
}

function Meter({ label, used, total, unit }: { label: string; used: number; total: number; unit: string }) {
  const percent = total ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return (
    <div className="compute-meter">
      <div className="compute-meter-head"><span>{label}</span><em>{used}{unit === '%' ? '%' : ` / ${total} ${unit}`}</em></div>
      <div className="compute-meter-track"><div className="compute-meter-fill" style={{ width: `${percent}%` }} /></div>
    </div>
  );
}

function gib(megabytes?: number | null): string {
  return megabytes == null ? '—' : (megabytes / 1024).toFixed(1);
}

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function ScienceNotice({ title, detail, spinning, children }: {
  title: string; detail: string; spinning?: boolean; children?: React.ReactNode;
}) {
  return (
    <div className="science-notice">
      {spinning ? <Loader2 className="science-spinner" /> : null}
      <strong>{title}</strong>
      <p>{detail}</p>
      {children}
    </div>
  );
}
