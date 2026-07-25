import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  addEdge,
  Background,
  BackgroundVariant,
  NodeToolbar,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../style/canvas.css';

import { Boxes, ChevronDown, Copy, Download, Play, Save, Share2, Square, Trash2, UploadCloud, Workflow } from 'lucide-react';

import ShadTooltip from '../components/common/shadTooltipComponent';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuShortcut, DropdownMenuTrigger,
} from '../components/ui/dropdown-menu';

import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { EDGE_TYPES } from '../CustomEdges';
import { CanvasControls } from './CanvasControls';
import ConnectionLine from './ConnectionLine';
import { HelperLines, getHelperLines, type HelperLinesState } from './HelperLines';
import { MountRosterContext } from './CapabilityPicker';
import { NodePanel } from './NodePanel';
import { PlaygroundPanel } from './PlaygroundPanel';
import { NODE_ACTION_EVENT, NODE_TYPES } from './nodes';
import { Palette } from './Palette';
import { RunInputDialog, type RunInputField } from './RunInputDialog';
import { useUndoRedo } from './useUndoRedo';
import {
  CONTAINER_H,
  CONTAINER_W,
  DND_MIME,
  REF_PATTERN,
  freshId,
  nextPlacement,
  portsCompatible,
  specKeyFor,
  type CanvasData,
  type CanvasNode,
  type FlowGraphDoc,
  type FlowStatus,
  type FlowSummary,
  type FrameDoc,
  type FrameState,
  type GraphNodeDoc,
  type InvocationDoc,
  type MountRosters,
  type NodeSpec,
  type PortType,
  type RequestFn,
  type RunData,
} from './types';

const IO_INPUT_PORT: Record<string, PortType> = { string: 'text', array: 'list', object: 'object', number: 'text', boolean: 'text' };

/** Resolve a node's port type. io-input nodes type their ``out`` by input_type;
 * everything else reads the spec's inputs/outputs. */
function portTypeOf(node: CanvasNode, portName: string | null | undefined, side: 'in' | 'out'): PortType {
  if (!portName) return 'any';
  if (side === 'out' && node.data.kind === 'input') return IO_INPUT_PORT[node.data.io.input_type] ?? 'any';
  const ports = side === 'in' ? node.data.spec?.inputs : node.data.spec?.outputs;
  return ports?.find((port) => port.name === portName)?.type ?? 'any';
}

export default function CanvasView({ request, subscribe, sessionId, connected, theme, onNotice }: {
  request: RequestFn;
  subscribe: (listener: (event: import('../controllers/gateway').GatewayEvent) => void) => () => void;
  sessionId?: string;
  connected: boolean;
  theme: 'dark' | 'light';
  onNotice: (message: string) => void;
}) {
  const [specs, setSpecs] = useState<NodeSpec[]>([]);
  const [rosters, setRosters] = useState<MountRosters>({});
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [flowId, setFlowId] = useState('');
  const [flowName, setFlowName] = useState('Untitled flow');
  const [flowVersion, setFlowVersion] = useState('1.0.0');
  const [flowStatus, setFlowStatus] = useState<FlowStatus>();
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [runId, setRunId] = useState<string>();
  const [runOutput, setRunOutput] = useState<unknown>();
  const [runError, setRunError] = useState<string>();
  const [runData, setRunData] = useState<RunData>();
  const [inspected, setInspected] = useState<string>();
  const [playgroundOpen, setPlaygroundOpen] = useState(false);
  const [runDialog, setRunDialog] = useState<{ fields: RunInputField[] }>();
  const [dirty, setDirty] = useState(false);
  const specIndexRef = useRef(new Map<string, NodeSpec>());
  const flowInstanceRef = useRef<ReactFlowInstance<CanvasNode, Edge>>();
  const flowWrapRef = useRef<HTMLDivElement>(null);
  const [helperLines, setHelperLines] = useState<HelperLinesState>({});
  const graphRef = useRef<{ nodes: CanvasNode[]; edges: Edge[] }>({ nodes: [], edges: [] });
  const clipboardRef = useRef<{ nodes: CanvasNode[]; edges: Edge[] }>();
  // saveFlow is declared further down; the node-action handler reaches it here.
  const saveFlowRef = useRef<(() => void) | undefined>(undefined);
  const runFlowRef = useRef<(() => void) | undefined>(undefined);
  const runDataRef = useRef<RunData | undefined>(undefined);
  useEffect(() => { graphRef.current = { nodes, edges }; }, [nodes, edges]);

  const updateNodeData = useCallback((nodeId: string, patch: Partial<CanvasData> | ((data: CanvasData) => Partial<CanvasData>)) => {
    setDirty(true);
    setNodes((current) => current.map((node) => node.id === nodeId
      ? { ...node, data: { ...node.data, ...(typeof patch === 'function' ? patch(node.data) : patch) } }
      : node));
  }, [setNodes]);

  const { takeSnapshot, undo, redo, reset: resetHistory } = useUndoRedo(
    useCallback(() => graphRef.current, []),
    useCallback((snapshot) => { setNodes(snapshot.nodes); setEdges(snapshot.edges); setDirty(true); }, [setNodes, setEdges]),
  );

  const loadCatalog = useCallback(async () => {
    if (!sessionId) return;
    const [catalog, flowList] = await Promise.all([
      request('canvas.catalog'),
      request('canvas.flow.list', { session_id: sessionId }),
    ]);
    if (catalog.ok && Array.isArray(catalog.result.nodes)) {
      const list = catalog.result.nodes as NodeSpec[];
      setSpecs(list);
      specIndexRef.current = new Map(list.map((spec) => [spec.id, spec]));
    }
    if (catalog.ok && catalog.result.mounts && typeof catalog.result.mounts === 'object') {
      setRosters(catalog.result.mounts as MountRosters);
    }
    if (flowList.ok && Array.isArray(flowList.result.flows)) setFlows(flowList.result.flows as FlowSummary[]);
  }, [request, sessionId]);

  useEffect(() => {
    if (connected && sessionId) void loadCatalog().catch((error) => onNotice(String(error instanceof Error ? error.message : error)));
  }, [connected, sessionId, loadCatalog, onNotice]);

  // ----- graph <-> React Flow ---------------------------------------------

  const boundParamsFor = useCallback((nodeId: string, edgeList: Edge[]): Set<string> => {
    return new Set(edgeList.filter((edge) => edge.target === nodeId && edge.targetHandle).map((edge) => edge.targetHandle as string));
  }, []);

  const addFromSpec = useCallback((spec: NodeSpec, position?: { x: number; y: number }) => {
    const id = freshId();
    const data: CanvasData = {
      spec,
      kind: spec.category === 'io' ? (spec.id === 'io/input' ? 'input' : 'output') : 'step',
      stepType: spec.step_type ?? undefined,
      target: spec.target ?? (spec.category === 'structural' ? '' : undefined),
      task: '', args: {}, items: '', attrs: {},
      io: { name: '', input_type: 'string', required: false, default: '', description: '', value: '' },
      mounts: {},
      boundParams: new Set(),
      update: updateNodeData,
    };
    const fallback = nextPlacement();
    // Click-to-add (no drop point): drop the node at the CENTER of the current
    // viewport so it lands where the user is looking, not at a fixed flow
    // coordinate that may be scrolled off-screen. Small jitter avoids stacking.
    let placed = position;
    if (!placed) {
      const instance = flowInstanceRef.current;
      const wrap = flowWrapRef.current;
      if (instance && wrap) {
        const rect = wrap.getBoundingClientRect();
        const center = instance.screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
        placed = { x: center.x - 150 + (fallback % 5) * 28, y: center.y - 40 + (fallback % 5) * 28 };
      }
    }
    const node: CanvasNode = {
      id,
      type: spec.category === 'io' ? 'ioNode' : spec.container ? 'containerNode' : 'stepNode',
      position: placed ?? { x: 160 + (fallback % 4) * 80, y: 100 + (fallback % 6) * 70 },
      data,
    };
    setDirty(true);
    setNodes((current) => [...current, node]);
  }, [setNodes, updateNodeData]);

  const addNote = useCallback(() => {
    const id = freshId();
    const data: CanvasData = {
      kind: 'step', task: '', args: {}, items: '', attrs: {},
      io: { name: '', input_type: 'string', required: false, default: '', description: '', value: '' },
      mounts: {}, boundParams: new Set(), update: updateNodeData,
      note: { text: '', color: 'amber' },
    };
    let placed: { x: number; y: number } | undefined;
    const instance = flowInstanceRef.current;
    const wrap = flowWrapRef.current;
    if (instance && wrap) {
      const rect = wrap.getBoundingClientRect();
      const center = instance.screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
      placed = { x: center.x - 110, y: center.y - 70 };
    }
    takeSnapshot();
    setNodes((current) => [
      ...current.map((node) => ({ ...node, selected: false })),
      { id, type: 'noteNode', position: placed ?? { x: 200, y: 200 }, width: 220, height: 150, data, selected: true } as CanvasNode,
    ]);
    setDirty(true);
  }, [setNodes, updateNodeData, takeSnapshot]);

  useEffect(() => {
    const onAddNote = () => addNote();
    window.addEventListener('canvas-add-note', onAddNote);
    return () => window.removeEventListener('canvas-add-note', onAddNote);
  }, [addNote]);

  useEffect(() => {
    const onMinimizeAll = () => {
      setNodes((current) => {
        const anyExpanded = current.some((node) => node.type === 'stepNode' && !node.data.minimized);
        return current.map((node) => (node.type === 'stepNode' ? { ...node, data: { ...node.data, minimized: anyExpanded } } : node));
      });
      setDirty(true);
    };
    window.addEventListener('canvas-minimize-all', onMinimizeAll);
    return () => window.removeEventListener('canvas-minimize-all', onMinimizeAll);
  }, [setNodes]);

  const onCanvasDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    const specId = event.dataTransfer.getData(DND_MIME);
    const spec = specId ? specIndexRef.current.get(specId) : undefined;
    const instance = flowInstanceRef.current;
    if (!spec || !instance) return;
    takeSnapshot();
    const position = instance.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    addFromSpec(spec, { x: position.x - 130, y: position.y - 24 });
  }, [addFromSpec, takeSnapshot]);

  // ----- copy / paste / duplicate / delete (Langflow shortcut set) ----------

  const copySelection = useCallback(() => {
    const { nodes: currentNodes, edges: currentEdges } = graphRef.current;
    const selected = currentNodes.filter((node) => node.selected);
    if (!selected.length) return false;
    const ids = new Set(selected.map((node) => node.id));
    clipboardRef.current = {
      nodes: selected,
      edges: currentEdges.filter((edge) => ids.has(edge.source) && ids.has(edge.target) && !edge.id.startsWith('ref-')),
    };
    return true;
  }, []);

  const pasteClipboard = useCallback(() => {
    const clipboard = clipboardRef.current;
    if (!clipboard?.nodes.length) return;
    takeSnapshot();
    const idMap = new Map<string, string>();
    for (const node of clipboard.nodes) idMap.set(node.id, freshId());
    const cloned: CanvasNode[] = clipboard.nodes.map((node) => ({
      ...node,
      id: idMap.get(node.id)!,
      selected: true,
      parentId: node.parentId && idMap.has(node.parentId) ? idMap.get(node.parentId) : undefined,
      position: node.parentId && idMap.has(node.parentId) ? { ...node.position } : { x: node.position.x + 48, y: node.position.y + 48 },
      data: {
        ...node.data,
        args: { ...node.data.args }, attrs: { ...node.data.attrs }, io: { ...node.data.io }, mounts: { ...node.data.mounts },
        boundParams: new Set(node.data.boundParams), runState: undefined, runCount: undefined,
        update: updateNodeData,
      },
    }));
    const clonedEdges: Edge[] = clipboard.edges.map((edge) => ({
      ...edge,
      id: `e${idMap.get(edge.source)}-${idMap.get(edge.target)}-${(edge.targetHandle ?? '').replace(':', '_')}`,
      source: idMap.get(edge.source)!,
      target: idMap.get(edge.target)!,
    }));
    setDirty(true);
    setNodes((current) => [...current.map((node) => ({ ...node, selected: false })), ...cloned]
      .sort((left, right) => Number(Boolean(left.parentId)) - Number(Boolean(right.parentId))));
    setEdges((current) => [...current, ...clonedEdges]);
  }, [setEdges, setNodes, takeSnapshot, updateNodeData]);

  const duplicateNode = useCallback((nodeId: string) => {
    const node = graphRef.current.nodes.find((item) => item.id === nodeId);
    if (!node) return;
    clipboardRef.current = { nodes: [node], edges: [] };
    pasteClipboard();
  }, [pasteClipboard]);

  const deleteSelection = useCallback(() => {
    const ids = new Set(graphRef.current.nodes.filter((node) => node.selected).map((node) => node.id));
    if (!ids.size) return;
    takeSnapshot();
    setNodes((current) => current.filter((node) => !ids.has(node.id)));
    setEdges((current) => current.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)));
    setDirty(true);
  }, [setNodes, setEdges, takeSnapshot]);

  const deleteNode = useCallback((nodeId: string) => {
    takeSnapshot();
    setDirty(true);
    setNodes((current) => current.filter((node) => node.id !== nodeId && node.parentId !== nodeId));
    setEdges((current) => current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
    setInspected((current) => (current === nodeId ? undefined : current));
  }, [setEdges, setNodes, takeSnapshot]);

  const copyNode = useCallback((nodeId: string) => {
    const node = graphRef.current.nodes.find((item) => item.id === nodeId);
    if (node) clipboardRef.current = { nodes: [node], edges: [] };
  }, []);

  const minimizeNode = useCallback((nodeId: string) => {
    setDirty(true);
    setNodes((current) => current.map((node) =>
      node.id === nodeId ? { ...node, data: { ...node.data, minimized: !node.data.minimized } } : node));
  }, [setNodes]);

  // Freeze: pin the node's last run output so it is reused (and not re-executed)
  // on the next run; unfreeze clears it. The compiler inlines a frozen output.
  runDataRef.current = runData;
  const freezeNode = useCallback((nodeId: string) => {
    const invocations = runDataRef.current?.invocations ?? {};
    const captured = Object.values(invocations).find((inv) => inv.key.split(':').pop() === nodeId)?.output;
    setDirty(true);
    setNodes((current) => current.map((node) => {
      if (node.id !== nodeId) return node;
      if (node.data.frozen) return { ...node, data: { ...node.data, frozen: false, frozenOutput: undefined } };
      return { ...node, data: { ...node.data, frozen: true, frozenOutput: captured ?? node.data.frozenOutput } };
    }));
  }, [setNodes]);

  const downloadNode = useCallback((nodeId: string) => {
    const node = graphRef.current.nodes.find((item) => item.id === nodeId);
    if (!node) return;
    const d = node.data;
    const payload = {
      id: node.id, type: node.type, position: node.position,
      data: { kind: d.kind, stepType: d.stepType, target: d.target, task: d.task,
        args: d.args, items: d.items, attrs: d.attrs, mounts: d.mounts, io: d.io },
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${nodeId}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }, []);

  useEffect(() => {
    const onAction = (event: Event) => {
      const detail = (event as CustomEvent<{ nodeId: string; action: string }>).detail;
      if (!detail) return;
      if (detail.action === 'duplicate') duplicateNode(detail.nodeId);
      else if (detail.action === 'delete') deleteNode(detail.nodeId);
      else if (detail.action === 'copy') copyNode(detail.nodeId);
      else if (detail.action === 'docs') setInspected(detail.nodeId);
      else if (detail.action === 'minimize') minimizeNode(detail.nodeId);
      else if (detail.action === 'download') downloadNode(detail.nodeId);
      else if (detail.action === 'freeze') freezeNode(detail.nodeId);
      else if (detail.action === 'save') saveFlowRef.current?.();
      else if (detail.action === 'run') runFlowRef.current?.();
    };
    window.addEventListener(NODE_ACTION_EVENT, onAction);
    return () => window.removeEventListener(NODE_ACTION_EVENT, onAction);
  }, [duplicateNode, deleteNode, copyNode, minimizeNode, downloadNode, freezeNode]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable);
      const mod = event.ctrlKey || event.metaKey;
      if (!mod || editing) return;
      const key = event.key.toLowerCase();
      if (key === 'z' && !event.shiftKey) { event.preventDefault(); undo(); }
      else if ((key === 'z' && event.shiftKey) || key === 'y') { event.preventDefault(); redo(); }
      else if (key === 'c') { if (copySelection()) event.preventDefault(); }
      else if (key === 'v') { event.preventDefault(); pasteClipboard(); }
      else if (key === 'd') { if (copySelection()) { event.preventDefault(); pasteClipboard(); } }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [undo, redo, copySelection, pasteClipboard]);

  const toDocument = useCallback((): FlowGraphDoc => {
    const containerOf = new Map(nodes.map((node) => [node.id, node]));
    return {
      id: flowId, name: flowName.trim() || 'Untitled flow', description: '',
      version: flowVersion, document_version: 2, published: false, program_hash: '',
      nodes: nodes.filter((node) => node.type !== 'noteNode').map((node) => {
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
          if (data.name?.trim()) doc.name = data.name.trim();
          doc.step_type = data.stepType;
          doc.target = data.target || null;
          doc.task = data.task;
          doc.items = data.items;
          doc.args = Object.fromEntries(Object.entries(data.args).filter(([, value]) => String(value).trim() !== ''));
          doc.attrs = Object.fromEntries(Object.entries(data.attrs).filter(([, value]) => String(value).trim() !== ''));
          const mountEntries = Object.entries(data.mounts).filter(([, names]) => names.length);
          if (mountEntries.length) doc.mounts = Object.fromEntries(mountEntries);
          if (data.frozen) { doc.frozen = true; if (data.frozenOutput !== undefined) doc.frozen_output = data.frozenOutput; }
        } else {
          doc.name = data.io.name; doc.input_type = data.io.input_type; doc.required = data.io.required;
          doc.default = data.io.default || null; doc.description = data.io.description; doc.value = data.io.value;
        }
        return doc;
      }),
      edges: edges.filter((edge) => !edge.id.startsWith('ref-')).map((edge) => ({
        id: edge.id, source: edge.source, target: edge.target, param: edge.targetHandle ?? '',
        source_port: (edge.data as { sourcePort?: string } | undefined)?.sourcePort ?? 'out',
      })),
      notes: nodes.filter((node) => node.type === 'noteNode').map((node) => ({
        id: node.id,
        position: { x: node.position.x, y: node.position.y },
        width: node.width ?? 220,
        height: node.height ?? 150,
        text: node.data.note?.text ?? '',
        color: node.data.note?.color ?? 'amber',
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
        name: item.kind === 'step' ? (item.name ?? undefined) : undefined,
        stepType: item.step_type ?? spec?.step_type ?? undefined,
        target: item.target ?? spec?.target ?? undefined,
        task: item.task ?? '',
        args: Object.fromEntries(Object.entries(item.args ?? {}).map(([key, value]) => [key, String(value ?? '')])),
        items: item.items ?? '',
        attrs: Object.fromEntries(Object.entries(item.attrs ?? {}).map(([key, value]) => [key, String(value ?? '')])),
        io: { name: item.name ?? '', input_type: item.input_type ?? 'string', required: Boolean(item.required), default: item.default == null ? '' : String(item.default), description: item.description ?? '', value: item.value ?? '' },
        mounts: item.mounts ?? {},
        frozen: item.frozen ?? false,
        frozenOutput: item.frozen_output,
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
    // Sticky notes ride alongside the graph (doc.notes), never inside it.
    for (const note of doc.notes ?? []) {
      restored.push({
        id: note.id,
        type: 'noteNode',
        position: note.position,
        width: note.width,
        height: note.height,
        data: {
          kind: 'step', task: '', args: {}, items: '', attrs: {},
          io: { name: '', input_type: 'string', required: false, default: '', description: '', value: '' },
          mounts: {}, boundParams: new Set(), update: updateNodeData,
          note: { text: note.text, color: note.color },
        },
      } as CanvasNode);
    }
    restored.sort((left, right) => Number(Boolean(left.parentId)) - Number(Boolean(right.parentId)));
    const restoredEdges: Edge[] = doc.edges.map((edge) => ({
      // The rendered source handle is always "out"; the sub-path lives in edge
      // data (migration: pre-typed edges have no source_port → whole value).
      id: edge.id, source: edge.source, sourceHandle: 'out', target: edge.target, targetHandle: edge.param,
      data: { sourcePort: edge.source_port ?? 'out' },
    }));
    for (const node of restored) node.data.boundParams = boundParamsFor(node.id, restoredEdges);
    setNodes(restored);
    setEdges(restoredEdges);
  }, [boundParamsFor, setEdges, setNodes, updateNodeData]);

  // ----- edges: bindings + derived reference display ------------------------

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.targetHandle || !connection.source || !connection.target || connection.source === connection.target) return;
    const target = nodes.find((node) => node.id === connection.target);
    const source = nodes.find((node) => node.id === connection.source);
    if (!target || !source) return;
    if (edges.some((edge) => edge.target === connection.target && edge.targetHandle === connection.targetHandle && !edge.id.startsWith('ref-'))) {
      onNotice('That input is already connected; remove the existing edge first.');
      return;
    }
    const targetType = portTypeOf(target, connection.targetHandle, 'in');
    // Capability nodes (>1 output) are polymorphic: one adaptive handle whose
    // sub-path is inferred from the target's type. Others carry one typed output.
    const adaptive = (source.data.spec?.outputs?.length ?? 0) > 1;
    const sourceType = adaptive ? 'any' : portTypeOf(source, 'out', 'out');
    if (!portsCompatible(sourceType, targetType)) {
      onNotice(`Type mismatch: a ${sourceType} output can't connect to a ${targetType} input.`);
      return;
    }
    const sourcePort = adaptive
      ? (({ text: 'message', object: 'data', list: 'files' } as Record<string, string>)[targetType] ?? 'message')
      : 'out';
    const param = connection.targetHandle;
    if (param.startsWith('arg:') && String(target.data.args[param.slice(4)] ?? '').trim()) {
      onNotice('That parameter has a literal value; clear it before connecting.');
      return;
    }
    if (param === 'items' && target.data.items.trim()) {
      onNotice('Items already has a literal reference; clear it before connecting.');
      return;
    }
    takeSnapshot();
    setDirty(true);
    const id = `e${connection.source}-${connection.target}-${param.replace(':', '_')}`;
    setEdges((current) => {
      const next = addEdge({ ...connection, id, data: { sourcePort } }, current);
      setNodes((currentNodes) => currentNodes.map((node) => node.id === connection.target
        ? { ...node, data: { ...node.data, boundParams: boundParamsFor(node.id, next) } } : node));
      return next;
    });
  }, [nodes, edges, boundParamsFor, onNotice, setEdges, setNodes, takeSnapshot]);

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
      const center = { x: absolute.x + 130, y: absolute.y + 40 };
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
    if (!sessionId) return;
    const flowList = await request('canvas.flow.list', { session_id: sessionId });
    if (flowList.ok && Array.isArray(flowList.result.flows)) setFlows(flowList.result.flows as FlowSummary[]);
  }, [request, sessionId]);

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
  saveFlowRef.current = () => { void saveFlow(); };

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
      onNotice(`Published as workflow "${workflowName}" v${version} — now a callable capability.`);
      await refreshFlows();
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const openFlow = async (id: string) => {
    if (!id) { newFlow(); return; }
    try {
      const response = await request('canvas.flow.get', { flow_id: id, session_id: sessionId });
      if (!response.ok) throw new Error(response.error?.message ?? 'Could not open the flow');
      const doc = response.result.flow as FlowGraphDoc;
      setFlowId(doc.id); setFlowName(doc.name); setFlowVersion(doc.version);
      setFlowStatus(response.result.status as FlowStatus);
      fromDocument(doc);
      resetHistory();
      setRunOutput(undefined); setRunError(undefined); setRunId(undefined); setRunData(undefined); setInspected(undefined); setDirty(false);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const deleteFlow = async () => {
    if (!flowId || !window.confirm(`Delete flow "${flowName}"?${flowStatus?.registered ? ' Its registered workflow will be unregistered too.' : ''}`)) return;
    const response = await request('canvas.flow.delete', { flow_id: flowId, session_id: sessionId });
    if (!response.ok) { onNotice(response.error?.message ?? 'Could not delete the flow'); return; }
    newFlow();
    await refreshFlows();
  };

  const newFlow = () => {
    setFlowId(''); setFlowName('Untitled flow'); setFlowVersion('1.0.0'); setFlowStatus(undefined);
    setNodes([]); setEdges([]); resetHistory(); setRunOutput(undefined); setRunError(undefined); setRunId(undefined); setRunData(undefined); setInspected(undefined); setDirty(false);
  };

  // ----- runs ----------------------------------------------------------------

  const startRun = async (input: Record<string, unknown>): Promise<string | undefined> => {
    if (!sessionId) { onNotice('Connect a session before running a flow.'); return undefined; }
    setRunOutput(undefined); setRunError(undefined); setRunData(undefined);
    setNodes((current) => current.map((node) => ({ ...node, data: { ...node.data, runState: undefined, runCount: undefined } })));
    try {
      const response = await request('canvas.flow.run', { session_id: sessionId, flow: toDocument(), input });
      if (!response.ok || typeof response.result.run_id !== 'string') throw new Error(response.error?.message ?? 'Could not start the run');
      setRunId(response.result.run_id);
      return response.result.run_id;
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
      return undefined;
    }
  };

  const runFlow = () => {
    const inputFields: RunInputField[] = nodes.filter((node) => node.data.kind === 'input' && node.data.io.name).map((node) => ({
      name: node.data.io.name, type: node.data.io.input_type, required: node.data.io.required, value: node.data.io.default,
    }));
    if (inputFields.length) setRunDialog({ fields: inputFields });
    else void startRun({});
  };
  runFlowRef.current = runFlow;

  const exportFlow = () => {
    const blob = new Blob([JSON.stringify(toDocument(), null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${flowName.trim() || 'flow'}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
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

  // ----- render --------------------------------------------------------------

  const displayEdges = useMemo(() => [...edges, ...refEdges], [edges, refEdges]);
  const selectedNodeIds = nodes.filter((node) => node.selected).map((node) => node.id);
  const inspectedNode = inspected ? nodes.find((node) => node.id === inspected) : undefined;

  return (
    <MountRosterContext.Provider value={rosters}>
    <div className="canvas-view">
      <Palette specs={specs} connected={connected} onAdd={(spec) => { takeSnapshot(); addFromSpec(spec); }} />
      <div className="canvas-stage">
        <header className="canvas-toolbar">
          {/* Flow identity — Langflow FlowMenu: colored icon swatch + a picker
              breadcrumb + a semibold name field. */}
          <div className="flex shrink-0 items-center justify-center rounded bg-muted p-1 text-primary"><Workflow className="h-3.5 w-3.5" /></div>
          <Select value={flowId || '__new__'} onValueChange={(next) => void openFlow(next === '__new__' ? '' : next)}>
            <SelectTrigger className="h-8 w-[170px] text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__new__" className="text-xs">＋ New flow</SelectItem>
              {flows.map((flow) => <SelectItem key={flow.id} value={flow.id} className="text-xs">{flow.published ? '● ' : '○ '}{flow.name} v{flow.version}</SelectItem>)}
            </SelectContent>
          </Select>
          <Input className="h-8 w-[min(240px,26vw)] text-sm font-semibold" value={flowName} onChange={(event) => { setFlowName(event.target.value); setDirty(true); }} placeholder="Flow name" />
          {flowStatus?.registered ? <em className={`canvas-badge${flowStatus.drifted ? ' drift' : ''}`} title={flowStatus.drifted ? 'The registered workflow changed outside the canvas; publishing overwrites it.' : `Registered as ${flowStatus.workflow_name}`}>{flowStatus.drifted ? '⚠ drifted' : `● ${flowStatus.workflow_name} v${flowStatus.registered_version}`}</em> : null}
          <span className="canvas-toolbar-spacer" />
          {/* Langflow flow-toolbar hierarchy: exactly one filled accent (Run);
              everything else ghost + font-normal. Secondary actions consolidate
              under a ghost "Share ▾" dropdown (deploy-dropdown.tsx). */}
          {dirty ? (
            <Button variant="ghost" size="md" className="font-normal text-muted-foreground" onClick={() => void saveFlow()} disabled={!connected || !nodes.length} title="Save draft (⌘S)"><Save /> Save</Button>
          ) : null}
          <Button variant={playgroundOpen ? 'ghostActive' : 'ghost'} size="md" className="font-normal" onClick={() => { setPlaygroundOpen((open) => !open); setInspected(undefined); }} disabled={!connected}><Play /> Playground</Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="md" className="!px-2.5 font-normal"><Share2 /> Share <ChevronDown className="h-4 w-4" /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuItem onClick={() => void saveFlow()} disabled={!connected || !nodes.length}><Save className="mr-2 h-4 w-4" />Save draft<DropdownMenuShortcut>⌘S</DropdownMenuShortcut></DropdownMenuItem>
              <DropdownMenuItem onClick={() => void publishFlow()} disabled={!connected || !nodes.length}><UploadCloud className="mr-2 h-4 w-4" />Publish as workflow</DropdownMenuItem>
              <DropdownMenuItem onClick={exportFlow} disabled={!nodes.length}><Download className="mr-2 h-4 w-4" />Export JSON</DropdownMenuItem>
              {flowId ? <>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={() => void deleteFlow()}><Trash2 className="mr-2 h-4 w-4" />Delete flow</DropdownMenuItem>
              </> : null}
            </DropdownMenuContent>
          </DropdownMenu>
          {runId
            ? <Button variant="destructive" size="md" className="font-normal" onClick={() => void stopRun()}><Square /> Stop</Button>
            : <Button size="md" className="font-normal" onClick={runFlow} disabled={!connected || !nodes.length}><Play /> Run</Button>}
        </header>
        <div ref={flowWrapRef} className="canvas-flow-wrap" onDrop={onCanvasDrop} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}>
          <ReactFlow
            nodes={nodes}
            edges={displayEdges}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            onInit={(instance) => { flowInstanceRef.current = instance; }}
            onNodesChange={(changes) => { onNodesChange(changes); if (changes.some((change) => change.type === 'remove')) setDirty(true); }}
            onEdgesChange={(changes) => {
              onEdgesChange(changes.filter((change) => !(change.type === 'remove' && change.id.startsWith('ref-'))));
              if (changes.some((change) => change.type === 'remove')) {
                setDirty(true);
                setNodes((current) => current.map((node) => ({ ...node, data: { ...node.data, boundParams: boundParamsFor(node.id, edges.filter((edge) => !changes.some((change) => change.type === 'remove' && change.id === edge.id))) } })));
              }
            }}
            onConnect={onConnect}
            onNodeDragStart={() => takeSnapshot()}
            onNodeDrag={(_event, node) => setHelperLines(getHelperLines(node, nodes))}
            onNodeDragStop={(event, node) => { setHelperLines({}); onNodeDragStop(event, node); }}
            connectionLineComponent={ConnectionLine}
            onNodeClick={(_event, node) => { if (node.type !== 'noteNode' && node.data.kind === 'step') { setInspected(node.id); setPlaygroundOpen(false); } }}
            onNodeContextMenu={(event, node) => {
              // Langflow onNodeContextMenu: select only this node, then open its
              // toolbar dropdown (the CardToolbar ⋯ menu) at the cursor.
              event.preventDefault();
              setNodes((current) => current.map((entry) => ({ ...entry, selected: entry.id === node.id })));
              window.dispatchEvent(new CustomEvent('canvas-node-menu', { detail: { nodeId: node.id } }));
            }}
            onPaneClick={() => setInspected(undefined)}
            onBeforeDelete={async () => { takeSnapshot(); return true; }}
            colorMode={theme}
            fitView
            fitViewOptions={{ minZoom: 0.25, maxZoom: 2 }}
            minZoom={0.25}
            maxZoom={2}
            selectNodesOnDrag={false}
            elevateEdgesOnSelect={false}
            connectionRadius={30}
            deleteKeyCode={['Backspace', 'Delete']}
            proOptions={{ hideAttribution: true }}
          >
            <Background id="main-canvas-bg" gap={20} size={2} variant={BackgroundVariant.Dots} />
            <CanvasControls />
            <HelperLines helperLines={helperLines} />
            {selectedNodeIds.length > 1 ? (
              // Floating toolbar over a multi-selection (Langflow SelectionMenu).
              <NodeToolbar nodeId={selectedNodeIds} isVisible position={Position.Top} offset={8}>
                <div className="lf-node-toolbar">
                  <ShadTooltip content={`Duplicate ${selectedNodeIds.length} nodes`}><Button variant="ghost" size="node-toolbar" onClick={() => { if (copySelection()) pasteClipboard(); }}><Copy /></Button></ShadTooltip>
                  <ShadTooltip content={`Delete ${selectedNodeIds.length} nodes`}><Button variant="ghost" size="node-toolbar" className="text-destructive" onClick={deleteSelection}><Trash2 /></Button></ShadTooltip>
                </div>
              </NodeToolbar>
            ) : null}
          </ReactFlow>
          {!nodes.length ? (
            <div className="canvas-empty">
              <Boxes strokeWidth={1.25} />
              <h3>Start building your flow</h3>
              <p>Drag a component from the left, or hover a component and click <strong>+</strong> to drop it here.</p>
            </div>
          ) : null}
          {playgroundOpen ? (
            <PlaygroundPanel
              request={request}
              subscribe={subscribe}
              sessionId={sessionId}
              connected={connected}
              onNotice={onNotice}
              onClose={() => setPlaygroundOpen(false)}
              inputNodes={nodes.filter((node) => node.data.kind === 'input' && node.data.io.name).map((node) => ({
                name: node.data.io.name, input_type: node.data.io.input_type, required: node.data.io.required, default: node.data.io.default,
              }))}
              startRun={startRun}
              runId={runId}
              runData={runData}
              runOutput={runOutput}
              runError={runError}
            />
          ) : inspectedNode ? <NodePanel node={inspectedNode} runData={runData} onClose={() => setInspected(undefined)} /> : null}
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
        <RunInputDialog fields={runDialog.fields} onCancel={() => setRunDialog(undefined)}
          onRun={(input) => { setRunDialog(undefined); void startRun(input); }} />
      ) : null}
    </div>
    </MountRosterContext.Provider>
  );
}
