import { Handle, NodeToolbar, Position, type NodeProps } from '@xyflow/react';
import { Copy, Trash2 } from 'lucide-react';

import { AgentMounts } from './CapabilityPicker';
import ShadTooltip from '../components/common/shadTooltipComponent';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Switch } from '../components/ui/switch';
import { Textarea } from '../components/ui/textarea';
import { CategoryIcon } from '../icons';
import { CONTAINER_H, CONTAINER_W, PORT_COLORS, type CanvasData, type CanvasNode, type NodeSpec, type ParamSpec, type PortSpec, type PortType } from './types';

function runClass(data: CanvasData): string { return data.runState ? ` run-${data.runState}` : ''; }

// Typed data-flow handles (Langflow-style colored dots): color = port type.
const IO_INPUT_PORT: Record<string, PortType> = { string: 'text', array: 'list', object: 'object', number: 'text', boolean: 'text' };
function portColor(type: PortType): string { return PORT_COLORS[type] ?? PORT_COLORS.any; }
function inputPortType(spec: NodeSpec | undefined, name: string): PortType {
  return spec?.inputs?.find((port) => port.name === name)?.type ?? 'any';
}

function InHandle({ id, type }: { id: string; type: PortType }) {
  return <Handle type="target" id={id} position={Position.Left} className="lf-handle in" style={{ background: portColor(type) }} title={`${id} · ${type}`} />;
}

/** A single output handle (Langflow-clean, one row). Capability nodes are
 * polymorphic — one adaptive port whose sub-path (message/data/files) is
 * inferred from what you connect it to; other nodes carry their one typed
 * output. ``ioType`` colors the io-input node's output by its declared type. */
function OutputPorts({ outputs, ioType }: { outputs?: PortSpec[]; ioType?: PortType }) {
  const adaptive = (outputs?.length ?? 0) > 1;
  const port = outputs?.[0] ?? { name: 'out', label: 'Result', type: 'any' as PortType };
  const type: PortType = adaptive ? 'any' : (ioType ?? port.type);
  const label = adaptive ? 'Output' : port.label;
  const hint = adaptive
    ? 'Output — the sub-value is chosen by what you connect it to (text→message, object→data, list→files)'
    : `${label} · ${type}`;
  return (
    <footer className="lf-node-foot">
      <div className="lf-out-row" title={hint}>
        <span>{label}</span>
        <Handle type="source" id="out" position={Position.Right} className="lf-handle out" style={{ background: portColor(type) }} title={hint} />
      </div>
    </footer>
  );
}

/** Selected-node action bar (Langflow's nodeToolbarComponent pattern). Node
 * components have no app handlers, so actions travel as a DOM CustomEvent
 * that the canvas root listens for. */
export const NODE_ACTION_EVENT = 'canvas-node-action';
function emitNodeAction(nodeId: string, action: 'duplicate' | 'delete') {
  window.dispatchEvent(new CustomEvent(NODE_ACTION_EVENT, { detail: { nodeId, action } }));
}

function CardToolbar({ id, visible }: { id: string; visible: boolean }) {
  return (
    <NodeToolbar isVisible={visible} position={Position.Top} offset={8}>
      <div className="lf-node-toolbar">
        <ShadTooltip content="Duplicate (Ctrl+D)"><Button variant="ghost" size="node-toolbar" onClick={() => emitNodeAction(id, 'duplicate')}><Copy /></Button></ShadTooltip>
        <ShadTooltip content="Delete (Del)"><Button variant="ghost" size="node-toolbar" className="hover:text-destructive" onClick={() => emitNodeAction(id, 'delete')}><Trash2 /></Button></ShadTooltip>
      </div>
    </NodeToolbar>
  );
}

export function FieldShell({ label, required, hint, handle, children }: { label: string; required?: boolean; hint?: string; handle?: React.ReactNode; children: React.ReactNode }) {
  // Langflow node mechanics: each field is a full-width row flush to the node
  // border; the handle sits at the row's left edge (on the border), vertically
  // centered on the row via top:50%.
  return (
    <div className="lf-field" title={hint}>
      {handle}
      <span className="lf-field-label">{label}{required ? <em> *</em> : null}</span>
      {children}
    </div>
  );
}

export function ParamControl({ param, value, onChange }: { param: ParamSpec; value: string; onChange: (name: string, value: string) => void }) {
  if (param.type === 'boolean') {
    return <Switch checked={value === 'true'} onCheckedChange={(checked) => onChange(param.name, checked ? 'true' : '')} />;
  }
  if (param.type === 'select') {
    return (
      <Select value={value || undefined} onValueChange={(next) => onChange(param.name, next)}>
        <SelectTrigger className="h-8 text-xs nodrag"><SelectValue placeholder="Select…" /></SelectTrigger>
        <SelectContent>{(param.options ?? []).map((option) => <SelectItem key={option} value={option} className="text-xs">{option}</SelectItem>)}</SelectContent>
      </Select>
    );
  }
  if (param.multiline || param.type === 'json') {
    return <Textarea rows={2} className="min-h-0 px-2.5 py-1.5 text-xs" value={value} spellCheck={false} placeholder={param.type === 'json' ? '{ }' : 'Type something…'} onChange={(event) => onChange(param.name, event.target.value)} />;
  }
  return <Input type={param.type === 'number' ? 'number' : 'text'} className="h-8 px-2.5 text-xs" value={value} placeholder="Type something…" onChange={(event) => onChange(param.name, event.target.value)} />;
}

/** Structural specs edit `target`/`attrs`; capability specs edit `args`. */
export function paramAccess(id: string, data: CanvasData) {
  const spec = data.spec;
  const paramValue = (param: ParamSpec): string => {
    if (spec?.category === 'structural') return param.name === 'target' ? data.target ?? '' : data.attrs[param.name] ?? '';
    return data.args[param.name] ?? '';
  };
  const setParam = (name: string, value: string) => data.update(id, (current) => {
    if (spec?.category === 'structural') return name === 'target' ? { target: value } : { attrs: { ...current.attrs, [name]: value } };
    return { args: { ...current.args, [name]: value } };
  });
  return { paramValue, setParam };
}

function StepNodeCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const spec = data.spec;
  const { paramValue, setParam } = paramAccess(id, data);
  return (
    <div className={`lf-node${runClass(data)}${selected ? ' selected' : ''}`}>
      <CardToolbar id={id} visible={Boolean(selected)} />
      <header className="lf-node-head">
        <CategoryIcon category={spec?.category ?? 'tool'} />
        <strong>{spec?.label ?? data.stepType ?? 'Step'}</strong>
        {data.runState === 'running' ? <span className="lf-pulse" /> : null}
        {data.runCount && data.runCount > 1 ? <em className="lf-run-count">×{data.runCount}</em> : null}
        <code className="lf-node-ref" title="Reference this step in task text">{'$'}{'{'}{id}{'}'}</code>
      </header>
      {spec?.description ? <p className="lf-node-desc">{spec.description}</p> : null}
      <div className="lf-node-body nodrag nowheel">
        {spec?.has_items || data.items ? (
          <FieldShell label="Items" hint="A list to iterate: connect a step or type ${...}"
            handle={<InHandle id="items" type={inputPortType(spec, 'items')} />}>
            {data.boundParams.has('items')
              ? <span className="lf-bound">Connected</span>
              : <Input className="h-8 px-2.5 text-xs" value={data.items} placeholder="${inputs.list}" onChange={(event) => data.update(id, { items: event.target.value })} />}
          </FieldShell>
        ) : null}
        {spec?.has_task || data.task ? (
          <FieldShell label="Task" hint="Instruction text; embed results with ${step_id} and inputs with ${inputs.name}"
            handle={<InHandle id="task" type={inputPortType(spec, 'task')} />}>
            {data.boundParams.has('task')
              ? <span className="lf-bound">Connected</span>
              : <Textarea rows={3} className="min-h-0 px-2.5 py-1.5 text-xs" value={data.task} spellCheck={false} placeholder="Type something…" onChange={(event) => data.update(id, { task: event.target.value })} />}
          </FieldShell>
        ) : null}
        {(spec?.params ?? []).map((param) => (
          <FieldShell key={param.name} label={param.label} required={param.required} hint={param.description}
            handle={param.connectable ? <InHandle id={`arg:${param.name}`} type={inputPortType(spec, `arg:${param.name}`)} /> : undefined}>
            {data.boundParams.has(`arg:${param.name}`)
              ? <span className="lf-bound">Connected</span>
              : <ParamControl param={param} value={paramValue(param)} onChange={setParam} />}
          </FieldShell>
        ))}
        {spec?.mount_kinds?.length ? (
          <AgentMounts
            kinds={spec.mount_kinds}
            mounts={data.mounts}
            onChange={(kind, next) => data.update(id, (current) => ({ mounts: { ...current.mounts, [kind]: next } }))}
          />
        ) : null}
      </div>
      <OutputPorts outputs={spec?.outputs} />
      {data.runState === 'failed' ? <p className="lf-node-error">Failed — open the panel for details</p> : null}
    </div>
  );
}

function ContainerNodeCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const spec = data.spec;
  const { paramValue, setParam } = paramAccess(id, data);
  const isBranch = data.stepType === 'branch';
  return (
    <div className={`lf-node lf-container${runClass(data)}${selected ? ' selected' : ''}`} style={{ width: CONTAINER_W, height: CONTAINER_H }}>
      <CardToolbar id={id} visible={Boolean(selected)} />
      <header className="lf-node-head">
        <CategoryIcon category="structural" />
        <strong>{spec?.label ?? data.stepType}</strong>
        {data.runState === 'running' ? <span className="lf-pulse" /> : null}
        {data.runCount && data.runCount > 1 ? <em className="lf-run-count">×{data.runCount}</em> : null}
        <code className="lf-node-ref">{'$'}{'{'}{id}{'}'}</code>
      </header>
      <div className="lf-node-body nodrag nowheel">
        {spec?.has_items ? (
          <FieldShell label="Items" handle={<InHandle id="items" type={inputPortType(spec, 'items')} />}>
            {data.boundParams.has('items')
              ? <span className="lf-bound">Connected</span>
              : <Input className="h-8 px-2.5 text-xs" value={data.items} placeholder="${inputs.list}" onChange={(event) => data.update(id, { items: event.target.value })} />}
          </FieldShell>
        ) : null}
        {(spec?.params ?? []).slice(0, 2).map((param) => (
          <FieldShell key={param.name} label={param.label} required={param.required} hint={param.description}>
            <ParamControl param={param} value={paramValue(param)} onChange={setParam} />
          </FieldShell>
        ))}
      </div>
      <div className={`lf-container-body${isBranch ? ' branch' : ''}`}>
        {isBranch ? <><span className="zone-label then">then</span><span className="zone-label else">else</span><div className="zone-divider" /></> : <span className="zone-hint">Drop steps here</span>}
      </div>
      <OutputPorts outputs={spec?.outputs} />
    </div>
  );
}

function IoNodeCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const isInput = data.kind === 'input';
  const io = data.io;
  const set = (patch: Partial<CanvasData['io']>) => data.update(id, (current) => ({ io: { ...current.io, ...patch } }));
  return (
    <div className={`lf-node lf-io${selected ? ' selected' : ''}`}>
      <CardToolbar id={id} visible={Boolean(selected)} />
      <header className="lf-node-head">
        <CategoryIcon category="io" />
        <strong>{isInput ? 'Flow Input' : 'Flow Output'}</strong>
        {isInput && io.name ? <code className="lf-node-ref">{'$'}{'{'}inputs.{io.name}{'}'}</code> : null}
      </header>
      <div className="lf-node-body nodrag nowheel">
        <FieldShell label="Name" required><Input className="h-8 px-2.5 text-xs" value={io.name} placeholder="name" onChange={(event) => set({ name: event.target.value })} /></FieldShell>
        {isInput ? <>
          <FieldShell label="Type"><Select value={io.input_type} onValueChange={(next) => set({ input_type: next })}><SelectTrigger className="h-8 text-xs nodrag"><SelectValue /></SelectTrigger><SelectContent>{['string', 'number', 'boolean', 'array', 'object'].map((option) => <SelectItem key={option} value={option} className="text-xs">{option}</SelectItem>)}</SelectContent></Select></FieldShell>
          <FieldShell label="Required"><Switch checked={io.required} onCheckedChange={(checked) => set({ required: checked })} /></FieldShell>
        </> : (
          <FieldShell label="Value" handle={<InHandle id="value" type="any" />}>
            {data.boundParams.has('value')
              ? <span className="lf-bound">Connected</span>
              : <Input className="h-8 px-2.5 text-xs" value={io.value} placeholder="${step_id}" onChange={(event) => set({ value: event.target.value })} />}
          </FieldShell>
        )}
      </div>
      {isInput ? <OutputPorts outputs={data.spec?.outputs} ioType={IO_INPUT_PORT[io.input_type] ?? 'any'} /> : null}
    </div>
  );
}

export const NODE_TYPES = { stepNode: StepNodeCard, containerNode: ContainerNodeCard, ioNode: IoNodeCard };
