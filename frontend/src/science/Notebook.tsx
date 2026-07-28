import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CirclePlay, CircleStop, Plus, RotateCcw, Save, Trash2 } from 'lucide-react';

import { Button } from '../components/ui/button';
import type { RequestFn } from '../canvas/types';
import type { GatewayEvent } from '../controllers/gateway';

export interface KernelOutput { type: string; name?: string | null; data: Record<string, string> }
export interface Cell { id: string; type: string; source: string; outputs: KernelOutput[]; execution_count?: number | null }

/** MIME types we render as something other than text, most specific first —
 *  a bundle carries several representations of one thing and the first match
 *  is the richest one we can show. */
const RENDERERS = ['image/png', 'image/jpeg', 'image/svg+xml', 'text/html', 'text/markdown', 'application/json'] as const;

/** The notebook editor: our own cells, the workstation's kernel.
 *
 * The document is NOT ours — the Jupyter Server in the science container owns
 * it, and this is one of its clients exactly like the embedded Lab is. Reads go
 * through its contents API, saves carry the `last_modified` we were handed so a
 * save that would clobber an edit made in the Lab fails instead of winning
 * silently. See agentevolver/science/notebook.py. */
export function NotebookEditor({ request, subscribe, sessionId, path, onDirtyChange }: {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId: string;
  path: string;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [cells, setCells] = useState<Cell[]>([]);
  const [lastModified, setLastModified] = useState('');
  const [running, setRunning] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  const load = useCallback(async () => {
    setLoading(true);
    const response = await request('science.notebook.get', { session_id: sessionId, path });
    if (response.ok) {
      const body = response.result as unknown as { cells: Cell[]; last_modified: string };
      setCells(body.cells);
      setLastModified(body.last_modified);
      setDirty(false);
      setNotice(undefined);
    } else {
      setNotice(response.error?.message ?? 'Could not open this notebook');
    }
    setLoading(false);
  }, [request, sessionId, path]);

  useEffect(() => { void load(); }, [load]);

  // Outputs stream in while a cell runs, so a long loop shows its prints as it
  // goes rather than everything appearing when the call finally returns.
  useEffect(() => subscribe((event) => {
    if (event.type !== 'science.cell.output') return;
    const payload = event.payload as { path?: string; cell_id?: string; output?: KernelOutput };
    // Another notebook in the same project runs in its own kernel and streams
    // on the same socket, so match the path before appending.
    if (payload.path !== path || !payload.cell_id || !payload.output) return;
    setCells((current) => current.map((cell) => cell.id === payload.cell_id
      ? { ...cell, outputs: [...cell.outputs, payload.output as KernelOutput] } : cell));
  }), [subscribe, path]);

  const update = (id: string, source: string) => {
    setCells((current) => current.map((cell) => cell.id === id ? { ...cell, source } : cell));
    setDirty(true);
  };

  const run = async (cell: Cell) => {
    if (cell.type !== 'code') return;
    setRunning(cell.id);
    // Clear first: the streamed outputs append, so leaving the previous run's
    // there would show this run's prints under the last one's.
    setCells((current) => current.map((item) => item.id === cell.id ? { ...item, outputs: [] } : item));
    const response = await request('science.cell.run', { session_id: sessionId, path, cell_id: cell.id, code: cell.source });
    if (response.ok) {
      const body = response.result as unknown as { outputs: KernelOutput[]; execution_count?: number | null };
      // Replace with the authoritative list rather than keeping what streamed:
      // an event dropped by a reconnect would otherwise leave a gap.
      setCells((current) => current.map((item) => item.id === cell.id
        ? { ...item, outputs: body.outputs, execution_count: body.execution_count } : item));
      setDirty(true);
    } else {
      setNotice(response.error?.message ?? 'The cell could not run');
    }
    setRunning(undefined);
  };

  const save = async () => {
    const response = await request('science.notebook.save', {
      session_id: sessionId, path, cells, last_modified: lastModified,
    });
    if (!response.ok) { setNotice(response.error?.message ?? 'Could not save'); return; }
    const body = response.result as unknown as { saved: boolean; conflict: boolean; message?: string; last_modified?: string };
    if (body.conflict) {
      // Not overwritten and not merged — the user decides, because we cannot
      // know which version they want.
      setNotice(`${body.message ?? 'This notebook changed elsewhere.'} Reload to take the other version, discarding your edits here.`);
      return;
    }
    setLastModified(body.last_modified ?? '');
    setDirty(false);
    setNotice(undefined);
  };

  const addCell = (type: 'code' | 'markdown') => {
    // A client-side id is enough: nbformat 4.5 wants a unique string, and the
    // server keeps whatever it is given.
    const id = `c${Math.random().toString(36).slice(2, 10)}`;
    setCells((current) => [...current, { id, type, source: '', outputs: [] }]);
    setDirty(true);
  };

  const removeCell = (id: string) => {
    setCells((current) => current.filter((cell) => cell.id !== id));
    setDirty(true);
  };

  const interrupt = () => void request('science.kernel.interrupt', { session_id: sessionId, path });
  const restart = async () => {
    await request('science.kernel.restart', { session_id: sessionId, path });
    setNotice('Kernel restarted — every variable is gone. Re-run the cells you need.');
  };

  if (loading) return <p className="notebook-empty">Opening {path}…</p>;

  return (
    <div className="notebook-editor">
      <header className="notebook-bar">
        <code title={path}>{path}</code>
        <span className="notebook-bar-spacer" />
        {dirty ? <em className="notebook-dirty">Unsaved</em> : null}
        <Button variant="ghost" size="sm" className="font-normal" onClick={() => void save()} disabled={!dirty}>
          <Save /> Save
        </Button>
        <Button variant="ghost" size="sm" className="font-normal" onClick={interrupt} disabled={!running}>
          <CircleStop /> Interrupt
        </Button>
        <Button variant="ghost" size="sm" className="font-normal" onClick={() => void restart()}>
          <RotateCcw /> Restart
        </Button>
      </header>
      {notice ? <p className="notebook-notice">{notice} <button onClick={() => void load()}>Reload</button></p> : null}
      <div className="notebook-cells">
        {cells.map((cell) => (
          <CellRow key={cell.id} cell={cell} running={running === cell.id}
                   onChange={(source) => update(cell.id, source)}
                   onRun={() => void run(cell)} onRemove={() => removeCell(cell.id)} />
        ))}
        <div className="notebook-add">
          <Button variant="ghost" size="sm" className="font-normal" onClick={() => addCell('code')}><Plus /> Code</Button>
          <Button variant="ghost" size="sm" className="font-normal" onClick={() => addCell('markdown')}><Plus /> Markdown</Button>
        </div>
      </div>
    </div>
  );
}

function CellRow({ cell, running, onChange, onRun, onRemove }: {
  cell: Cell; running: boolean;
  onChange: (source: string) => void; onRun: () => void; onRemove: () => void;
}) {
  const area = useRef<HTMLTextAreaElement>(null);
  // Grow to the content: a fixed-height box that scrolls internally makes a
  // 40-line cell unreadable, and the page already scrolls.
  useEffect(() => {
    const node = area.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${node.scrollHeight}px`;
  }, [cell.source]);

  return (
    <section className={`notebook-cell ${cell.type}${running ? ' running' : ''}`}>
      <div className="notebook-gutter">
        {cell.type === 'code' ? (
          <button onClick={onRun} disabled={running} title="Run this cell (⌘/Ctrl+Enter)" aria-label="Run cell">
            <CirclePlay size={15} strokeWidth={1.9} />
          </button>
        ) : null}
        <span className="notebook-count">{running ? '*' : cell.execution_count ?? ' '}</span>
        <button className="notebook-remove" onClick={onRemove} title="Delete this cell" aria-label="Delete cell">
          <Trash2 size={13} strokeWidth={1.9} />
        </button>
      </div>
      <div className="notebook-cell-body">
        <textarea
          ref={area}
          className="notebook-source"
          value={cell.source}
          spellCheck={false}
          placeholder={cell.type === 'markdown' ? 'Markdown…' : 'Python…'}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); onRun(); }
          }}
        />
        {cell.outputs.length ? (
          <div className="notebook-outputs">
            {cell.outputs.map((output, index) => <Output key={index} output={output} />)}
          </div>
        ) : null}
      </div>
    </section>
  );
}

/** One output, rendered by the richest representation its bundle carries. */
function Output({ output }: { output: KernelOutput }) {
  const mime = useMemo(() => RENDERERS.find((candidate) => output.data[candidate]), [output]);
  const text = output.data['text/plain'] ?? '';

  if (output.type === 'error') return <pre className="notebook-output error">{text}</pre>;

  if (mime === 'image/png' || mime === 'image/jpeg') {
    return <img className="notebook-output image" alt="Cell output" src={`data:${mime};base64,${output.data[mime]}`} />;
  }
  if (mime === 'image/svg+xml') {
    // As an <img> rather than inline: an SVG can carry script, and inlining it
    // would run that script on this app's origin, where the gateway token is.
    return <img className="notebook-output image" alt="Cell output"
                src={`data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(output.data[mime])))}`} />;
  }
  if (mime === 'text/html') {
    // A fully sandboxed iframe: kernel output is not trusted content — a
    // DataFrame can contain anything a dataset contains — and this page holds
    // the gateway connection. Scripts are off, so a plotly widget will not draw
    // here; that is what the JupyterLab entry in the toolbar is for.
    return <iframe className="notebook-output html" sandbox="" title="Cell output" srcDoc={output.data[mime]} />;
  }
  if (mime === 'application/json') {
    return <pre className="notebook-output">{JSON.stringify(JSON.parse(output.data[mime] || '{}'), null, 2)}</pre>;
  }
  if (mime === 'text/markdown') return <pre className="notebook-output">{output.data[mime]}</pre>;
  return text ? <pre className={`notebook-output${output.name === 'stderr' ? ' stderr' : ''}`}>{text}</pre> : null;
}
