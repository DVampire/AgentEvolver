import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  addEdge,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type { GatewayResponse } from './gateway';

// ---------------------------------------------------------------------------
// Wire contracts (mirror agentevolver/canvas/types.py)
// ---------------------------------------------------------------------------

interface ParamSpec { name: string; label: string; type: 'string' | 'number' | 'boolean' | 'select' | 'json'; required: boolean; default: unknown; options?: string[] | null; multiline: boolean; description: string; connectable: boolean; }
interface NodeSpec { id: string; category: 'tool' | 'agent' | 'workflow' | 'structural' | 'io'; step_type?: string | null; target?: string | null; label: string; description: string; params: ParamSpec[]; has_task: boolean; has_items: boolean; container: boolean; }
interface FlowSummary { id: string; name: string; description: string; version: string; published: boolean; updated_at?: string | null; node_count: number; }
interface FlowStatus { workflow_name: string; registered: boolean; registered_version?: string | null; drifted: boolean; }

interface GraphNodeDoc {
  id: string; kind: 'step' | 'input' | 'output';
  step_type?: string | null; target?: string | null; task?: string; args?: Record<string, unknown>; items?: string; attrs?: Record<string, unknown>;
  name?: string; input_type?: string; required?: boolean; default?: unknown; description?: string; value?: string;
  parent?: string | null; slot?: 'body' | 'then' | 'else';
  position: { x: number; y: number };
}
interface GraphEdgeDoc { id: string; source: string; target: string; param: string; }
interface FlowGraphDoc { id: string; name: string; description: string; version: string; document_version: number; nodes: GraphNodeDoc[]; edges: GraphEdgeDoc[]; published: boolean; program_hash: string; }

type RequestFn = (method: string, params?: Record<string, unknown>) => Promise<GatewayResponse>;
type FrameState = 'pending' | 'ready' | 'running' | 'retry_wait' | 'cached' | 'succeeded' | 'failed' | 'cancelled' | 'skipped';

interface FrameDoc { key: string; step_id: string; state: FrameState; item_index?: number | null; iteration?: number | null; output?: unknown; error?: string | null; started_at?: string | null; finished_at?: string | null; }
interface InvocationDoc { key: string; frame_key: string; capability_type: string; capability_name: string; state: string; input: Record<string, unknown>; attempts: Array<{ number: number; state: string; error?: string | null }>; output?: unknown; error?: string | null; cached: boolean; token_cost: number; started_at?: string | null; finished_at?: string | null; }
interface RunData { state: string; frames: Record<string, FrameDoc>; invocations: Record<string, InvocationDoc>; }

interface CanvasData extends Record<string, unknown> {
  spec?: NodeSpec;
  kind: 'step' | 'input' | 'output';
  stepType?: string;
  target?: string;
  task: string;
  args: Record<string, string>;
  items: string;
  attrs: Record<string, string>;
  io: { name: string; input_type: string; required: boolean; default: string; description: string; value: string };
  runState?: FrameState;
  runCount?: number;
  boundParams: Set<string>;
  update: (nodeId: string, patch: Partial<CanvasData> | ((data: CanvasData) => Partial<CanvasData>)) => void;
}
type CanvasNode = Node<CanvasData>;

const CONTAINER_W = 420;
const CONTAINER_H = 300;
const CATEGORY_ICONS: Record<string, string> = { io: '▷', structural: '⌘', tool: '⌥', agent: '✦', workflow: '⎇' };
const CATEGORY_ORDER = ['io', 'structural', 'tool', 'agent', 'workflow'];
const REF_PATTERN = /\$\{([A-Za-z][A-Za-z0-9_-]*)/g;

let placedCounter = 0;
function freshId(): string { placedCounter += 1; return `n${Date.now().toString(36)}${placedCounter}`; }

// ---------------------------------------------------------------------------
// Node components
// ---------------------------------------------------------------------------

function runClass(data: CanvasData): string { return data.runState ? ` run-${data.runState}` : ''; }

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="canvas-field" title={hint}><span>{label}</span>{children}</label>;
}

function ParamRow({ nodeId, param, value, bound, onChange }: { nodeId: string; param: ParamSpec; value: string; bound: boolean; onChange: (name: string, value: string) => void }) {
  const label = `${param.label}${param.required ? ' *' : ''}`;
  return (
    <div className="canvas-param-row">
      {param.connectable ? <Handle type="target" id={`arg:${param.name}`} position={Position.Left} className="port-arg" /> : null}
      <Field label={label} hint={param.description}>
        {bound
          ? <em className="canvas-bound">⌁ connected</em>
          : param.type === 'boolean'
            ? <input type="checkbox" checked={value === 'true'} onChange={(event) => onChange(param.name, event.target.checked ? 'true' : '')} />
            : param.type === 'select'
              ? <select value={value} onChange={(event) => onChange(param.name, event.target.value)}><option value="">—</option>{(param.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}</select>
              : param.multiline || param.type === 'json'
                ? <textarea rows={2} value={value} spellCheck={false} onChange={(event) => onChange(param.name, event.target.value)} />
                : <input type={param.type === 'number' ? 'number' : 'text'} value={value} onChange={(event) => onChange(param.name, event.target.value)} />}
      </Field>
    </div>
  );
}

function StepNodeCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const spec = data.spec;
  const isStructuralTarget = (name: string) => name === 'target';
  const paramValue = (param: ParamSpec): string => {
    if (spec?.category === 'structural') {
      return isStructuralTarget(param.name) ? data.target ?? '' : data.attrs[param.name] ?? '';
    }
    return data.args[param.name] ?? '';
  };
  const setParam = (name: string, value: string) => data.update(id, (current) => {
    if (spec?.category === 'structural') {
      return isStructuralTarget(name) ? { target: value } : { attrs: { ...current.attrs, [name]: value } };
    }
    return { args: { ...current.args, [name]: value } };
  });
  return (
    <div className={`canvas-node${runClass(data)}${selected ? ' selected' : ''}`}>
      <header className="canvas-node-head">
        <span className="canvas-node-icon">{CATEGORY_ICONS[spec?.category ?? 'tool']}</span>
        <strong>{spec?.label ?? data.stepType ?? 'step'}</strong>
        <code className="canvas-node-id" title="Reference this step as ${id} in task text">{'$'}{'{'}{id}{'}'}</code>
        {data.runState === 'running' ? <span className="canvas-node-pulse" /> : null}
        {data.runCount && data.runCount > 1 ? <em className="canvas-run-count">×{data.runCount}</em> : null}
      </header>
      <div className="canvas-node-form nodrag nowheel">
        {spec?.has_items || data.items ? (
          <div className="canvas-param-row">
            <Handle type="target" id="items" position={Position.Left} className="port-items" />
            <Field label="Items" hint="Connect a list-producing step or type a ${...} reference">
              {data.boundParams.has('items')
                ? <em className="canvas-bound">⌁ connected</em>
                : <input value={data.items} placeholder="${step_id} or ${inputs.name}" onChange={(event) => data.update(id, { items: event.target.value })} />}
            </Field>
          </div>
        ) : null}
        {spec?.has_task || data.task ? (
          <Field label="Task" hint="Instruction text; embed upstream results with ${step_id} and flow inputs with ${inputs.name}">
            <textarea rows={3} value={data.task} spellCheck={false} onChange={(event) => data.update(id, { task: event.target.value })} />
          </Field>
        ) : null}
        {(spec?.params ?? []).map((param) => (
          <ParamRow key={param.name} nodeId={id} param={param} bound={data.boundParams.has(`arg:${param.name}`)} value={paramValue(param)} onChange={setParam} />
        ))}
        <details className="canvas-advanced nodrag">
          <summary>Advanced</summary>
          <Field label="Retries"><input type="number" value={data.attrs.retries ?? ''} onChange={(event) => data.update(id, (current) => ({ attrs: { ...current.attrs, retries: event.target.value } }))} /></Field>
          <Field label="Timeout (s)"><input type="number" value={data.attrs.timeout ?? ''} onChange={(event) => data.update(id, (current) => ({ attrs: { ...current.attrs, timeout: event.target.value } }))} /></Field>
        </details>
      </div>
      <div className="canvas-node-out"><span>result</span><Handle type="source" id="out" position={Position.Right} className="port-out" /></div>
    </div>
  );
}

function ContainerNodeCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const spec = data.spec;
  const isBranch = data.stepType === 'branch';
  return (
    <div className={`canvas-container${runClass(data)}${selected ? ' selected' : ''}`} style={{ width: CONTAINER_W, height: CONTAINER_H }}>
      <header className="canvas-node-head">
        <span className="canvas-node-icon">⌘</span>
        <strong>{spec?.label ?? data.stepType}</strong>
        <code className="canvas-node-id">{'$'}{'{'}{id}{'}'}</code>
        {data.runState === 'running' ? <span className="canvas-node-pulse" /> : null}
        {data.runCount && data.runCount > 1 ? <em className="canvas-run-count">×{data.runCount}</em> : null}
      </header>
      <div className="canvas-container-form nodrag nowheel">
        {spec?.has_items ? (
          <div className="canvas-param-row">
            <Handle type="target" id="items" position={Position.Left} className="port-items" />
            {data.boundParams.has('items')
              ? <em className="canvas-bound">items ⌁ connected</em>
              : <input value={data.items} placeholder="items: ${inputs.list}" onChange={(event) => data.update(id, { items: event.target.value })} />}
          </div>
        ) : null}
        {(spec?.params ?? []).map((param) => (
          <ParamRow key={param.name} nodeId={id} param={param} bound={false} value={param.name === 'target' ? data.target ?? '' : data.attrs[param.name] ?? ''}
            onChange={(name, value) => data.update(id, (current) => name === 'target' ? { target: value } : { attrs: { ...current.attrs, [name]: value } })} />
        ))}
      </div>
      <div className={`canvas-container-body${isBranch ? ' branch' : ''}`}>
        {isBranch ? <><span className="zone-label then">then ↑</span><span className="zone-label else">else ↓</span><div className="zone-divider" /></> : <span className="zone-label">drop steps here</span>}
      </div>
      <div className="canvas-node-out"><span>result</span><Handle type="source" id="out" position={Position.Right} className="port-out" /></div>
    </div>
  );
}

function IoNodeCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const isInput = data.kind === 'input';
  const io = data.io;
  const set = (patch: Partial<CanvasData['io']>) => data.update(id, (current) => ({ io: { ...current.io, ...patch } }));
  return (
    <div className={`canvas-node canvas-io${selected ? ' selected' : ''}`}>
      <header className="canvas-node-head">
        <span className="canvas-node-icon">{isInput ? '▷' : '◉'}</span>
        <strong>{isInput ? 'Flow input' : 'Flow output'}</strong>
        {isInput && io.name ? <code className="canvas-node-id">{'$'}{'{'}inputs.{io.name}{'}'}</code> : null}
      </header>
      <div className="canvas-node-form nodrag nowheel">
        <Field label="Name *"><input value={io.name} onChange={(event) => set({ name: event.target.value })} /></Field>
        {isInput ? <>
          <Field label="Type"><select value={io.input_type} onChange={(event) => set({ input_type: event.target.value })}>{['string', 'number', 'boolean', 'array', 'object'].map((option) => <option key={option} value={option}>{option}</option>)}</select></Field>
          <Field label="Required"><input type="checkbox" checked={io.required} onChange={(event) => set({ required: event.target.checked })} /></Field>
          <Field label="Default"><input value={io.default} onChange={(event) => set({ default: event.target.value })} /></Field>
        </> : (
          <div className="canvas-param-row">
            <Handle type="target" id="value" position={Position.Left} className="port-arg" />
            {data.boundParams.has('value')
              ? <em className="canvas-bound">value ⌁ connected</em>
              : <Field label="Value"><input value={io.value} placeholder="${step_id}" onChange={(event) => set({ value: event.target.value })} /></Field>}
          </div>
        )}
      </div>
      {isInput ? <div className="canvas-node-out"><span>value</span><Handle type="source" id="out" position={Position.Right} className="port-out" /></div> : null}
    </div>
  );
}

const NODE_TYPES = { stepNode: StepNodeCard, containerNode: ContainerNodeCard, ioNode: IoNodeCard };

// ---------------------------------------------------------------------------
// Node run inspector — per-frame / per-invocation results for one step
// ---------------------------------------------------------------------------

function previewText(value: unknown): string {
  if (value === undefined || value === null) return '';
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return text.length > 20_000 ? `${text.slice(0, 20_000)}\n… (${text.length} chars)` : text;
}

function frameDuration(frame: { started_at?: string | null; finished_at?: string | null }): string {
  if (!frame.started_at || !frame.finished_at) return '';
  const ms = new Date(frame.finished_at).getTime() - new Date(frame.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function InspectorSection({ label, value }: { label: string; value: unknown }) {
  const text = previewText(value);
  if (!text) return null;
  return (
    <section className="inspector-section">
      <header><strong>{label}</strong><button onClick={() => navigator.clipboard?.writeText(text)}>Copy</button></header>
      <pre>{text}</pre>
    </section>
  );
}

function NodeInspector({ nodeId, label, runData, onClose }: { nodeId: string; label: string; runData?: RunData; onClose: () => void }) {
  const frames = Object.values(runData?.frames ?? {})
    .filter((frame) => frame.step_id === nodeId)
    .sort((left, right) => (left.item_index ?? 0) - (right.item_index ?? 0) || (left.iteration ?? 0) - (right.iteration ?? 0));
  const invocationsByFrame = new Map<string, InvocationDoc[]>();
  for (const invocation of Object.values(runData?.invocations ?? {})) {
    (invocationsByFrame.get(invocation.frame_key) ?? invocationsByFrame.set(invocation.frame_key, []).get(invocation.frame_key)!)
      .push(invocation);
  }
  return (
    <aside className="canvas-inspector nodrag nowheel">
      <header className="canvas-inspector-head">
        <strong>{label}</strong>
        <code>{nodeId}</code>
        <button onClick={onClose} aria-label="Close inspector">×</button>
      </header>
      <div className="canvas-inspector-body">
        {!runData ? <p className="empty">Run the flow to inspect this step's inputs and outputs.</p> : null}
        {runData && !frames.length ? <p className="empty">This step has not executed in the last run.</p> : null}
        {frames.map((frame) => {
          const invocations = invocationsByFrame.get(frame.key) ?? [];
          const title = [
            frame.item_index !== null && frame.item_index !== undefined ? `item ${frame.item_index}` : '',
            frame.iteration !== null && frame.iteration !== undefined ? `round ${frame.iteration}` : '',
          ].filter(Boolean).join(' · ') || 'execution';
          return (
            <details className={`inspector-frame ${frame.state}`} key={frame.key} open={frames.length === 1}>
              <summary>
                <span className={`frame-dot ${frame.state}`} />
                <strong>{title}</strong>
                <em>{frame.state}</em>
                <small>{frameDuration(frame)}</small>
              </summary>
              {invocations.map((invocation) => (
                <div className="inspector-invocation" key={invocation.key}>
                  <p className="inspector-meta">
                    {invocation.capability_type}:{invocation.capability_name}
                    {invocation.cached ? ' · cached' : ''}
                    {invocation.token_cost ? ` · ${invocation.token_cost} tokens` : ''}
                    {invocation.attempts.length > 1 ? ` · ${invocation.attempts.length} attempts` : ''}
                  </p>
                  <InspectorSection label="Input" value={invocation.input} />
                  <InspectorSection label="Output" value={invocation.output} />
                  <InspectorSection label="Error" value={invocation.error} />
                </div>
              ))}
              {!invocations.length ? <>
                <InspectorSection label="Output" value={frame.output} />
                <InspectorSection label="Error" value={frame.error} />
              </> : null}
            </details>
          );
        })}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Canvas panel
// ---------------------------------------------------------------------------

export default function CanvasView({ request, sessionId, connected, theme, onNotice }: {
  request: RequestFn;
  sessionId?: string;
  connected: boolean;
  theme: 'dark' | 'light';
  onNotice: (message: string) => void;
}) {
  const [specs, setSpecs] = useState<NodeSpec[]>([]);
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [flowId, setFlowId] = useState('');
  const [flowName, setFlowName] = useState('Untitled flow');
  const [flowVersion, setFlowVersion] = useState('1.0.0');
  const [flowStatus, setFlowStatus] = useState<FlowStatus>();
  const [search, setSearch] = useState('');
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [runId, setRunId] = useState<string>();
  const [runOutput, setRunOutput] = useState<unknown>();
  const [runError, setRunError] = useState<string>();
  const [runData, setRunData] = useState<RunData>();
  const [inspected, setInspected] = useState<string>();
  const [runDialog, setRunDialog] = useState<{ fields: Array<{ name: string; type: string; required: boolean; value: string }> }>();
  const [dirty, setDirty] = useState(false);
  const specIndexRef = useRef(new Map<string, NodeSpec>());

  const updateNodeData = useCallback((nodeId: string, patch: Partial<CanvasData> | ((data: CanvasData) => Partial<CanvasData>)) => {
    setDirty(true);
    setNodes((current) => current.map((node) => node.id === nodeId
      ? { ...node, data: { ...node.data, ...(typeof patch === 'function' ? patch(node.data) : patch) } }
      : node));
  }, [setNodes]);

  const specKeyFor = (doc: GraphNodeDoc): string => doc.kind !== 'step' ? `io/${doc.kind}`
    : ['tool', 'agent', 'workflow'].includes(doc.step_type ?? '') ? `${doc.step_type}/${doc.target ?? ''}` : `step/${doc.step_type}`;

  const loadCatalog = useCallback(async () => {
    const [catalog, flowList] = await Promise.all([request('canvas.catalog'), request('canvas.flow.list')]);
    if (catalog.ok && Array.isArray(catalog.result.nodes)) {
      const list = catalog.result.nodes as NodeSpec[];
      setSpecs(list);
      specIndexRef.current = new Map(list.map((spec) => [spec.id, spec]));
    }
    if (flowList.ok && Array.isArray(flowList.result.flows)) setFlows(flowList.result.flows as FlowSummary[]);
  }, [request]);

  useEffect(() => {
    if (connected) void loadCatalog().catch((error) => onNotice(String(error instanceof Error ? error.message : error)));
  }, [connected, loadCatalog, onNotice]);

  // ----- graph <-> React Flow ---------------------------------------------

  const boundParamsFor = useCallback((nodeId: string, edgeList: Edge[]): Set<string> => {
    return new Set(edgeList.filter((edge) => edge.target === nodeId && edge.targetHandle).map((edge) => edge.targetHandle as string));
  }, []);

  const addFromSpec = (spec: NodeSpec) => {
    const id = freshId();
    const data: CanvasData = {
      spec,
      kind: spec.category === 'io' ? (spec.id === 'io/input' ? 'input' : 'output') : 'step',
      stepType: spec.step_type ?? undefined,
      target: spec.target ?? (spec.category === 'structural' ? '' : undefined),
      task: '', args: {}, items: '', attrs: {},
      io: { name: '', input_type: 'string', required: false, default: '', description: '', value: '' },
      boundParams: new Set(),
      update: updateNodeData,
    };
    const node: CanvasNode = {
      id,
      type: spec.category === 'io' ? 'ioNode' : spec.container ? 'containerNode' : 'stepNode',
      position: { x: 140 + (placedCounter % 4) * 70, y: 90 + (placedCounter % 6) * 60 },
      data,
    };
    setDirty(true);
    setNodes((current) => [...current, node]);
  };

  const toDocument = useCallback((): FlowGraphDoc => {
    const containerOf = new Map(nodes.map((node) => [node.id, node]));
    return {
      id: flowId, name: flowName.trim() || 'Untitled flow', description: '',
      version: flowVersion, document_version: 2, published: false, program_hash: '',
      nodes: nodes.map((node) => {
        const data = node.data;
        const parent = node.parentId ?? null;
        let slot: 'body' | 'then' | 'else' = 'body';
        if (parent) {
          const container = containerOf.get(parent);
          if (container?.data.stepType === 'branch') slot = node.position.y > CONTAINER_H / 2 ? 'else' : 'then';
        }
        const doc: GraphNodeDoc = {
          id: node.id, kind: data.kind,
          position: { x: node.position.x, y: node.position.y },
          parent, slot,
        };
        if (data.kind === 'step') {
          doc.step_type = data.stepType;
          doc.target = data.target || null;
          doc.task = data.task;
          doc.items = data.items;
          doc.args = Object.fromEntries(Object.entries(data.args).filter(([, value]) => String(value).trim() !== ''));
          doc.attrs = Object.fromEntries(Object.entries(data.attrs).filter(([, value]) => String(value).trim() !== ''));
        } else {
          doc.name = data.io.name; doc.input_type = data.io.input_type; doc.required = data.io.required;
          doc.default = data.io.default || null; doc.description = data.io.description; doc.value = data.io.value;
        }
        return doc;
      }),
      edges: edges.filter((edge) => !edge.id.startsWith('ref-')).map((edge) => ({
        id: edge.id, source: edge.source, target: edge.target, param: edge.targetHandle ?? '',
      })),
    };
  }, [nodes, edges, flowId, flowName, flowVersion]);

  const fromDocument = useCallback((doc: FlowGraphDoc) => {
    const restored: CanvasNode[] = [];
    for (const item of doc.nodes) {
      const spec = specIndexRef.current.get(specKeyFor(item));
      const data: CanvasData = {
        spec,
        kind: item.kind,
        stepType: item.step_type ?? spec?.step_type ?? undefined,
        target: item.target ?? spec?.target ?? undefined,
        task: item.task ?? '',
        args: Object.fromEntries(Object.entries(item.args ?? {}).map(([key, value]) => [key, String(value ?? '')])),
        items: item.items ?? '',
        attrs: Object.fromEntries(Object.entries(item.attrs ?? {}).map(([key, value]) => [key, String(value ?? '')])),
        io: { name: item.name ?? '', input_type: item.input_type ?? 'string', required: Boolean(item.required), default: item.default == null ? '' : String(item.default), description: item.description ?? '', value: item.value ?? '' },
        boundParams: new Set(),
        update: updateNodeData,
      };
      restored.push({
        id: item.id,
        type: item.kind !== 'step' ? 'ioNode' : spec?.container || ['map', 'branch', 'loop'].includes(item.step_type ?? '') ? 'containerNode' : 'stepNode',
        position: item.position,
        parentId: item.parent ?? undefined,
        data,
      });
    }
    restored.sort((left, right) => Number(Boolean(left.parentId)) - Number(Boolean(right.parentId)));
    const restoredEdges: Edge[] = doc.edges.map((edge) => ({
      id: edge.id, source: edge.source, sourceHandle: 'out', target: edge.target, targetHandle: edge.param,
    }));
    for (const node of restored) node.data.boundParams = boundParamsFor(node.id, restoredEdges);
    setNodes(restored);
    setEdges(restoredEdges);
  }, [boundParamsFor, setEdges, setNodes, updateNodeData]);

  // ----- edges: bindings + derived reference display ------------------------

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.targetHandle || !connection.source || !connection.target || connection.source === connection.target) return;
    const target = nodes.find((node) => node.id === connection.target);
    if (!target) return;
    if (edges.some((edge) => edge.target === connection.target && edge.targetHandle === connection.targetHandle && !edge.id.startsWith('ref-'))) {
      onNotice('That input is already connected; remove the existing edge first.');
      return;
    }
    const param = connection.targetHandle;
    if (param.startsWith('arg:') && String(target.data.args[param.slice(4)] ?? '').trim()) {
      onNotice('That parameter has a literal value; clear it before connecting.');
      return;
    }
    if (param === 'items' && target.data.items.trim()) {
      onNotice('Items already has a literal reference; clear it before connecting.');
      return;
    }
    setDirty(true);
    const id = `e${connection.source}-${connection.target}-${param.replace(':', '_')}`;
    setEdges((current) => {
      const next = addEdge({ ...connection, id }, current);
      setNodes((currentNodes) => currentNodes.map((node) => node.id === connection.target
        ? { ...node, data: { ...node.data, boundParams: boundParamsFor(node.id, next) } } : node));
      return next;
    });
  }, [nodes, edges, boundParamsFor, onNotice, setEdges, setNodes]);

  const refEdges = useMemo(() => {
    const ids = new Set(nodes.map((node) => node.id));
    const derived: Edge[] = [];
    for (const node of nodes) {
      const texts = [node.data.task, node.data.items, ...Object.values(node.data.args), ...Object.values(node.data.attrs)];
      const seen = new Set<string>();
      for (const text of texts) {
        for (const match of String(text ?? '').matchAll(REF_PATTERN)) {
          const source = match[1];
          if (!ids.has(source) || source === node.id || seen.has(source)) continue;
          seen.add(source);
          derived.push({
            id: `ref-${source}-${node.id}`, source, sourceHandle: 'out', target: node.id, targetHandle: undefined,
            className: 'canvas-ref-edge', selectable: false, focusable: false, animated: true,
          } as Edge);
        }
      }
    }
    const persisted = new Set(edges.map((edge) => `${edge.source}->${edge.target}`));
    return derived.filter((edge) => !persisted.has(`${edge.source}->${edge.target}`));
  }, [nodes, edges]);

  // ----- container parenting on drag stop -----------------------------------

  const onNodeDragStop = useCallback((_event: unknown, dragged: Node) => {
    setDirty(true);
    setNodes((current) => {
      const node = current.find((item) => item.id === dragged.id);
      if (!node || node.type === 'containerNode') return current;
      const parent = node.parentId ? current.find((item) => item.id === node.parentId) : undefined;
      const absolute = parent
        ? { x: parent.position.x + node.position.x, y: parent.position.y + node.position.y }
        : node.position;
      const center = { x: absolute.x + 110, y: absolute.y + 40 };
      const host = current.find((item) => item.type === 'containerNode'
        && center.x > item.position.x && center.x < item.position.x + CONTAINER_W
        && center.y > item.position.y && center.y < item.position.y + CONTAINER_H);
      let next = current;
      if (host && node.parentId !== host.id) {
        next = current.map((item) => item.id === node.id
          ? { ...item, parentId: host.id, position: { x: absolute.x - host.position.x, y: absolute.y - host.position.y } }
          : item);
      } else if (!host && node.parentId) {
        next = current.map((item) => item.id === node.id ? { ...item, parentId: undefined, position: absolute } : item);
      }
      return [...next].sort((left, right) => Number(Boolean(left.parentId)) - Number(Boolean(right.parentId)));
    });
  }, [setNodes]);

  // ----- persistence ---------------------------------------------------------

  const refreshFlows = useCallback(async () => {
    const flowList = await request('canvas.flow.list');
    if (flowList.ok && Array.isArray(flowList.result.flows)) setFlows(flowList.result.flows as FlowSummary[]);
  }, [request]);

  const saveFlow = async (): Promise<string | undefined> => {
    try {
      const response = await request('canvas.flow.save', { flow: toDocument(), session_id: sessionId });
      if (!response.ok) throw new Error(response.error?.message ?? 'Could not save the flow');
      const saved = response.result.flow as FlowGraphDoc;
      setFlowId(saved.id);
      setFlowVersion(saved.version);
      setFlowStatus(response.result.status as FlowStatus);
      setDirty(false);
      await refreshFlows();
      return saved.id;
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
      return undefined;
    }
  };

  const publishFlow = async () => {
    const id = await saveFlow();
    if (!id) return;
    try {
      const response = await request('canvas.flow.publish', { flow_id: id, session_id: sessionId });
      if (!response.ok) throw new Error(response.error?.message ?? 'Publish failed');
      const version = String(response.result.version ?? '');
      const workflowName = String(response.result.workflow_name ?? '');
      setFlowVersion(version);
      setFlowStatus({ workflow_name: workflowName, registered: true, registered_version: version, drifted: false });
      onNotice(`Published as workflow "${workflowName}" v${version} — it is now a callable capability.`);
      await refreshFlows();
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const openFlow = async (id: string) => {
    if (!id) { newFlow(); return; }
    try {
      const response = await request('canvas.flow.get', { flow_id: id });
      if (!response.ok) throw new Error(response.error?.message ?? 'Could not open the flow');
      const doc = response.result.flow as FlowGraphDoc;
      setFlowId(doc.id); setFlowName(doc.name); setFlowVersion(doc.version);
      setFlowStatus(response.result.status as FlowStatus);
      fromDocument(doc);
      setRunOutput(undefined); setRunError(undefined); setRunId(undefined); setRunData(undefined); setInspected(undefined); setDirty(false);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const deleteFlow = async () => {
    if (!flowId || !window.confirm(`Delete flow "${flowName}"?${flowStatus?.registered ? ' Its registered workflow will be unregistered too.' : ''}`)) return;
    const response = await request('canvas.flow.delete', { flow_id: flowId });
    if (!response.ok) { onNotice(response.error?.message ?? 'Could not delete the flow'); return; }
    newFlow();
    await refreshFlows();
  };

  const newFlow = () => {
    setFlowId(''); setFlowName('Untitled flow'); setFlowVersion('1.0.0'); setFlowStatus(undefined);
    setNodes([]); setEdges([]); setRunOutput(undefined); setRunError(undefined); setRunId(undefined); setRunData(undefined); setInspected(undefined); setDirty(false);
  };

  // ----- runs ----------------------------------------------------------------

  const startRun = async (input: Record<string, unknown>) => {
    if (!sessionId) { onNotice('Connect a session before running a flow.'); return; }
    setRunOutput(undefined); setRunError(undefined); setRunData(undefined);
    setNodes((current) => current.map((node) => ({ ...node, data: { ...node.data, runState: undefined, runCount: undefined } })));
    try {
      const response = await request('canvas.flow.run', { session_id: sessionId, flow: toDocument(), input });
      if (!response.ok || typeof response.result.run_id !== 'string') throw new Error(response.error?.message ?? 'Could not start the run');
      setRunId(response.result.run_id);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const runFlow = () => {
    const inputFields = nodes.filter((node) => node.data.kind === 'input' && node.data.io.name).map((node) => ({
      name: node.data.io.name, type: node.data.io.input_type, required: node.data.io.required, value: node.data.io.default,
    }));
    if (inputFields.length) setRunDialog({ fields: inputFields });
    else void startRun({});
  };

  useEffect(() => {
    if (!runId) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await request('canvas.run.status', { run_id: runId });
        if (!response.ok) throw new Error(response.error?.message ?? 'status failed');
        const run = response.result.run as { state: string; output?: unknown; error?: string | null; frames?: Record<string, FrameDoc>; invocations?: Record<string, InvocationDoc> };
        setRunData({ state: run.state, frames: run.frames ?? {}, invocations: run.invocations ?? {} });
        const byStep = new Map<string, { state: FrameState; count: number }>();
        const precedence: FrameState[] = ['failed', 'running', 'retry_wait', 'ready', 'pending', 'cancelled', 'skipped', 'cached', 'succeeded'];
        for (const frame of Object.values(run.frames ?? {})) {
          if (!frame.step_id || !frame.state) continue;
          const existing = byStep.get(frame.step_id);
          const state = frame.state as FrameState;
          if (!existing) byStep.set(frame.step_id, { state, count: 1 });
          else byStep.set(frame.step_id, {
            state: precedence.indexOf(state) < precedence.indexOf(existing.state) ? state : existing.state,
            count: existing.count + 1,
          });
        }
        setNodes((current) => current.map((node) => {
          const info = byStep.get(node.id);
          return info ? { ...node, data: { ...node.data, runState: info.state, runCount: info.count } } : node;
        }));
        if (['succeeded', 'failed', 'cancelled'].includes(run.state)) {
          setRunId(undefined);
          if (run.state === 'succeeded') setRunOutput(run.output ?? null);
          else setRunError(run.error || `Run ${run.state}`);
        }
      } catch {
        // transient status errors: keep polling until the run resolves
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [runId, request, setNodes]);

  const stopRun = async () => { if (runId) await request('canvas.run.cancel', { run_id: runId }); };

  // ----- palette -------------------------------------------------------------

  const grouped = useMemo(() => {
    const query = search.trim().toLowerCase();
    const filtered = specs.filter((spec) => !query || spec.label.toLowerCase().includes(query) || spec.id.toLowerCase().includes(query));
    return CATEGORY_ORDER.map((category) => ({ category, items: filtered.filter((spec) => spec.category === category) })).filter((group) => group.items.length);
  }, [specs, search]);

  const displayEdges = useMemo(() => [...edges, ...refEdges], [edges, refEdges]);

  return (
    <div className="canvas-view">
      <aside className="canvas-catalog">
        <header><strong>Steps</strong><button title="Reload palette" onClick={() => void loadCatalog()}>↻</button></header>
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search steps…" />
        <div className="canvas-catalog-list">
          {grouped.map((group) => (
            <section key={group.category}>
              <p className="eyebrow">{CATEGORY_ICONS[group.category]} {group.category}</p>
              {group.items.map((spec) => (
                <button className="canvas-catalog-item" key={spec.id} title={spec.description} onClick={() => addFromSpec(spec)}>
                  <strong>{spec.label}</strong>
                  <small>{spec.description || spec.id}</small>
                </button>
              ))}
            </section>
          ))}
          {!grouped.length ? <p className="empty">{connected ? 'No steps match this search.' : 'Connecting…'}</p> : null}
        </div>
      </aside>
      <div className="canvas-stage">
        <header className="canvas-toolbar">
          <select value={flowId} onChange={(event) => void openFlow(event.target.value)} title="Open a saved flow">
            <option value="">＋ New flow</option>
            {flows.map((flow) => <option key={flow.id} value={flow.id}>{flow.published ? '● ' : '○ '}{flow.name} v{flow.version}</option>)}
          </select>
          <input className="canvas-flow-name" value={flowName} onChange={(event) => { setFlowName(event.target.value); setDirty(true); }} placeholder="Flow name" />
          {flowStatus?.registered ? <em className={`canvas-badge${flowStatus.drifted ? ' drift' : ''}`} title={flowStatus.drifted ? 'The registered workflow was changed outside the canvas; publishing will overwrite it.' : `Registered as ${flowStatus.workflow_name}`}>{flowStatus.drifted ? '⚠ drifted' : `● ${flowStatus.workflow_name} v${flowStatus.registered_version}`}</em> : null}
          <span className="canvas-toolbar-spacer" />
          {dirty ? <em className="canvas-dirty">unsaved</em> : null}
          <button onClick={() => void saveFlow()} disabled={!connected || !nodes.length}>Save draft</button>
          <button onClick={() => void publishFlow()} disabled={!connected || !nodes.length} title="Compile to workflow HTML and register it as a capability">⤴ Publish</button>
          {flowId ? <button className="danger" onClick={() => void deleteFlow()}>Delete</button> : null}
          {runId
            ? <button className="danger" onClick={() => void stopRun()}>■ Stop</button>
            : <button className="primary" onClick={runFlow} disabled={!connected || !nodes.length}>▶ Run</button>}
        </header>
        <div className="canvas-flow-wrap">
          <ReactFlow
            nodes={nodes}
            edges={displayEdges}
            nodeTypes={NODE_TYPES}
            onNodesChange={(changes) => { onNodesChange(changes); if (changes.some((change) => change.type === 'remove')) setDirty(true); }}
            onEdgesChange={(changes) => {
              onEdgesChange(changes.filter((change) => !(change.type === 'remove' && change.id.startsWith('ref-'))));
              if (changes.some((change) => change.type === 'remove')) {
                setDirty(true);
                setNodes((current) => current.map((node) => ({ ...node, data: { ...node.data, boundParams: boundParamsFor(node.id, edges.filter((edge) => !changes.some((change) => change.type === 'remove' && change.id === edge.id))) } })));
              }
            }}
            onConnect={onConnect}
            onNodeDragStop={onNodeDragStop}
            onNodeClick={(_event, node) => { if (node.data.kind === 'step') setInspected(node.id); }}
            onPaneClick={() => setInspected(undefined)}
            colorMode={theme}
            fitView
            deleteKeyCode={['Backspace', 'Delete']}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={18} />
            <MiniMap pannable zoomable />
            <Controls />
          </ReactFlow>
          {inspected ? (
            <NodeInspector
              nodeId={inspected}
              label={nodes.find((node) => node.id === inspected)?.data.spec?.label ?? inspected}
              runData={runData}
              onClose={() => setInspected(undefined)}
            />
          ) : null}
        </div>
        {runId || runOutput !== undefined || runError ? (
          <footer className={`canvas-results${runError ? ' failed' : ''}`}>
            {runId ? <span><span className="pulse" /> Running on the workflow runtime…</span>
              : runError ? <span>✕ {runError}</span>
                : <pre>{typeof runOutput === 'string' ? runOutput : JSON.stringify(runOutput, null, 2)}</pre>}
          </footer>
        ) : null}
      </div>
      {runDialog ? (
        <div className="canvas-run-dialog-backdrop" onClick={() => setRunDialog(undefined)}>
          <section className="canvas-run-dialog" onClick={(event) => event.stopPropagation()}>
            <h3>Flow inputs</h3>
            {runDialog.fields.map((field, index) => (
              <label key={field.name}><span>{field.name}{field.required ? ' *' : ''} <em>{field.type}</em></span>
                <textarea rows={field.type === 'array' || field.type === 'object' ? 3 : 1} value={field.value}
                  placeholder={field.type === 'array' ? '["a", "b"]' : field.type === 'object' ? '{ }' : ''}
                  onChange={(event) => setRunDialog((current) => current && ({ fields: current.fields.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item) }))} />
              </label>
            ))}
            <footer>
              <button onClick={() => setRunDialog(undefined)}>Cancel</button>
              <button className="primary" onClick={() => {
                const input: Record<string, unknown> = {};
                for (const field of runDialog.fields) {
                  if (!field.value.trim()) continue;
                  if (['array', 'object', 'number', 'boolean'].includes(field.type)) {
                    try { input[field.name] = JSON.parse(field.value); } catch { input[field.name] = field.value; }
                  } else input[field.name] = field.value;
                }
                setRunDialog(undefined);
                void startRun(input);
              }}>▶ Run</button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
