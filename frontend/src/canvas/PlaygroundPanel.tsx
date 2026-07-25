import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, Eraser, Play, Square, User, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Button } from '../components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Textarea } from '../components/ui/textarea';
import type { GatewayEvent } from '../controllers/gateway';
import type { FrameDoc, RequestFn, RunData } from './types';

// Langflow-style playground: opened from the canvas, its chat drives the
// CURRENT flow (each message = one run on the workflow runtime, with the
// execution record attached to the reply). A second tab chats directly with
// model_manager for quick LLM debugging.

interface FlowInputField { name: string; input_type: string; required: boolean; default: string; }
interface ExecutionStep { step: string; state: string; duration: string; }
interface FlowMessage { role: 'user' | 'assistant'; content: string; failed?: boolean; execution?: ExecutionStep[]; }
interface ModelMessage { role: 'user' | 'assistant'; content: string; }

const PREFERRED_INPUT_NAMES = ['message', 'input', 'question', 'task', 'query', 'prompt', 'text'];
const MODEL_KEY = 'agentevolver.playground.model';

function frameDuration(frame: { started_at?: string | null; finished_at?: string | null }): string {
  if (!frame.started_at || !frame.finished_at) return '';
  const ms = new Date(frame.finished_at).getTime() - new Date(frame.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function formatOutputs(output: unknown): string {
  if (output === null || output === undefined) return '(no output)';
  if (typeof output === 'string') return output;
  if (typeof output === 'object' && !Array.isArray(output)) {
    const entries = Object.entries(output as Record<string, unknown>);
    if (entries.length === 1) {
      const value = entries[0][1];
      return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    }
  }
  return JSON.stringify(output, null, 2);
}

// Langflow chatMessage transcript row: a full-width row with a 32px avatar,
// the sender name, then the message content — not left/right bubbles.
function Bubble({ role, failed, children }: { role: 'user' | 'assistant'; failed?: boolean; children: React.ReactNode }) {
  const isUser = role === 'user';
  return (
    <div className="group flex w-full gap-3 rounded-md p-2 hover:bg-muted/60">
      <div className="flex h-8 w-8 flex-none items-center justify-center rounded-md bg-muted text-foreground">
        {isUser ? <User size={16} /> : <Bot size={16} />}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-[13px] font-semibold text-foreground">{isUser ? 'User' : 'AI'}</span>
        <div className={`playground-markdown min-w-0 text-[13px] leading-relaxed ${failed ? 'text-destructive' : 'text-foreground'}`}>
          {children}
        </div>
      </div>
    </div>
  );
}

export function PlaygroundPanel({ request, subscribe, sessionId, connected, onNotice, onClose, inputNodes, startRun, runId, runData, runOutput, runError }: {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId?: string;
  connected: boolean;
  onNotice: (message: string) => void;
  onClose: () => void;
  inputNodes: FlowInputField[];
  startRun: (input: Record<string, unknown>) => Promise<string | undefined>;
  runId?: string;
  runData?: RunData;
  runOutput?: unknown;
  runError?: string;
}) {
  const [tab, setTab] = useState<'flow' | 'model'>('flow');
  const [flowMessages, setFlowMessages] = useState<FlowMessage[]>([]);
  const [flowInput, setFlowInput] = useState('');
  const [pendingRun, setPendingRun] = useState<string>();
  const endRef = useRef<HTMLDivElement>(null);

  // ----- flow chat: input mapping ------------------------------------------
  const targetInput = useMemo(() => {
    const strings = inputNodes.filter((field) => field.input_type === 'string');
    return strings.find((field) => PREFERRED_INPUT_NAMES.includes(field.name.toLowerCase())) ?? strings[0];
  }, [inputNodes]);

  const sendFlow = useCallback(async () => {
    const content = flowInput.trim();
    if (!content || pendingRun || runId) return;
    const input: Record<string, unknown> = {};
    for (const field of inputNodes) {
      if (targetInput && field.name === targetInput.name) input[field.name] = content;
      else if (field.default) {
        try { input[field.name] = ['array', 'object', 'number', 'boolean'].includes(field.input_type) ? JSON.parse(field.default) : field.default; }
        catch { input[field.name] = field.default; }
      }
    }
    setFlowMessages((current) => [...current, { role: 'user', content }]);
    setFlowInput('');
    const rid = await startRun(input);
    if (!rid) {
      setFlowMessages((current) => [...current, { role: 'assistant', content: 'The flow could not start — check the notice.', failed: true }]);
      return;
    }
    setPendingRun(rid);
  }, [flowInput, pendingRun, runId, inputNodes, targetInput, startRun]);

  // When the watched run settles, turn its outputs + frames into a reply.
  useEffect(() => {
    if (!pendingRun || runId) return;
    const execution: ExecutionStep[] = Object.values(runData?.frames ?? {}).map((frame: FrameDoc) => ({
      step: frame.step_id + (frame.item_index != null ? `[${frame.item_index}]` : '') + (frame.iteration != null ? ` r${frame.iteration}` : ''),
      state: frame.state,
      duration: frameDuration(frame),
    }));
    setFlowMessages((current) => [...current, runError
      ? { role: 'assistant', content: runError, failed: true, execution }
      : { role: 'assistant', content: formatOutputs(runOutput), execution }]);
    setPendingRun(undefined);
  }, [pendingRun, runId, runData, runOutput, runError]);

  // ----- model chat (direct model_manager) ----------------------------------
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState(() => localStorage.getItem(MODEL_KEY) ?? '');
  const [modelMessages, setModelMessages] = useState<ModelMessage[]>([]);
  const [modelInput, setModelInput] = useState('');
  const [streaming, setStreaming] = useState('');
  const [chatRequestId, setChatRequestId] = useState<string>();
  const chatRequestRef = useRef<string>();
  const streamRef = useRef('');
  chatRequestRef.current = chatRequestId;

  useEffect(() => { if (model) localStorage.setItem(MODEL_KEY, model); }, [model]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [flowMessages, modelMessages, streaming, tab, pendingRun]);

  useEffect(() => {
    if (!connected || tab !== 'model' || models.length) return;
    void (async () => {
      const response = await request('model.list');
      if (response.ok && Array.isArray(response.result.providers)) {
        const names = (response.result.providers as Array<{ models: Array<{ name: string }> }>).flatMap((provider) => provider.models.map((item) => item.name));
        setModels(names);
        if (names.length && !names.includes(model)) setModel(names[0]);
      }
    })().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, tab, request]);

  useEffect(() => {
    if (!connected) return;
    return subscribe((event) => {
      const payload = event.payload as Record<string, unknown>;
      if (!event.type.startsWith('model.chat.') || payload.request_id !== chatRequestRef.current) return;
      if (event.type === 'model.chat.delta' && typeof payload.text === 'string') {
        streamRef.current += payload.text;
        setStreaming(streamRef.current);
      } else if (event.type === 'model.chat.done') {
        const finalText = typeof payload.message === 'string' && payload.message ? payload.message : streamRef.current;
        setModelMessages((current) => [...current, { role: 'assistant', content: finalText }]);
        streamRef.current = ''; setStreaming(''); setChatRequestId(undefined);
      } else if (event.type === 'model.chat.cancelled') {
        if (streamRef.current) setModelMessages((current) => [...current, { role: 'assistant', content: streamRef.current }]);
        streamRef.current = ''; setStreaming(''); setChatRequestId(undefined);
      } else if (event.type === 'model.chat.error') {
        onNotice(String(payload.error ?? 'Model call failed'));
        streamRef.current = ''; setStreaming(''); setChatRequestId(undefined);
      }
    });
  }, [connected, subscribe, onNotice]);

  const sendModel = useCallback(async () => {
    const content = modelInput.trim();
    if (!content || !sessionId || !model || chatRequestId) return;
    const history = [...modelMessages, { role: 'user' as const, content }];
    setModelMessages(history);
    setModelInput('');
    streamRef.current = ''; setStreaming('');
    const response = await request('model.chat', { session_id: sessionId, model, messages: history });
    if (!response.ok || typeof response.result.request_id !== 'string') {
      onNotice(response.error?.message ?? 'Could not start the chat');
      return;
    }
    setChatRequestId(response.result.request_id);
  }, [modelInput, sessionId, model, chatRequestId, modelMessages, request, onNotice]);

  const flowBusy = Boolean(pendingRun || runId);

  return (
    <aside className="node-panel playground-panel nodrag nowheel">
      <header className="node-panel-head items-center">
        <div className="flex items-center gap-1.5">
          <Button variant={tab === 'flow' ? 'ghostActive' : 'ghost'} size="xs" onClick={() => setTab('flow')}>Flow</Button>
          <Button variant={tab === 'model' ? 'ghostActive' : 'ghost'} size="xs" onClick={() => setTab('model')}>Model</Button>
        </div>
        {tab === 'model' ? (
          <Select value={model || undefined} onValueChange={setModel}>
            <SelectTrigger className="ml-2 h-7 w-[190px] text-xs"><SelectValue placeholder="model…" /></SelectTrigger>
            <SelectContent className="max-h-72">{models.map((name) => <SelectItem key={name} value={name} className="text-xs">{name}</SelectItem>)}</SelectContent>
          </Select>
        ) : (
          <span className="ml-2 truncate text-xs text-muted-foreground">
            {targetInput ? `message → \${inputs.${targetInput.name}}` : 'runs the current flow'}
          </span>
        )}
        <Button variant="ghost" size="iconSm" className="ml-auto shrink-0" onClick={() => {
          if (tab === 'flow') setFlowMessages([]);
          else { setModelMessages([]); streamRef.current = ''; setStreaming(''); }
        }} title="Clear conversation"><Eraser /></Button>
        <Button variant="ghost" size="iconSm" className="shrink-0" onClick={onClose} aria-label="Close playground"><X /></Button>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-3">
        {tab === 'flow' ? <>
          {!flowMessages.length ? (
            <p className="mt-10 px-4 text-center text-xs text-muted-foreground">
              Each message runs the current flow on the workflow runtime.
              {targetInput ? ` Your text is passed as \${inputs.${targetInput.name}}.` : ' Add a string Flow Input to feed your message into the flow.'}
            </p>
          ) : null}
          {flowMessages.map((message, index) => (
            <Bubble key={index} role={message.role} failed={message.failed}>
              {message.role === 'user'
                ? <span className="whitespace-pre-wrap">{message.content}</span>
                : <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>}
              {message.execution?.length ? (
                <details className="mt-1.5 border-t border-border/50 pt-1.5 text-xs">
                  <summary className="cursor-pointer text-muted-foreground">Execution · {message.execution.length} steps</summary>
                  <ul className="mt-1 grid gap-0.5">
                    {message.execution.map((step, stepIndex) => (
                      <li key={stepIndex} className="flex items-center gap-2">
                        <span className={`frame-dot ${step.state}`} />
                        <code className="text-[11px]">{step.step}</code>
                        <em className="not-italic text-muted-foreground">{step.state}</em>
                        <small className="ml-auto text-muted-foreground">{step.duration}</small>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </Bubble>
          ))}
          {flowBusy ? <Bubble role="assistant"><span className="animate-pulse text-muted-foreground">running flow…</span></Bubble> : null}
        </> : <>
          {!modelMessages.length && !streaming ? (
            <p className="mt-10 px-4 text-center text-xs text-muted-foreground">Direct model_manager chat — no agent, no flow.</p>
          ) : null}
          {modelMessages.map((message, index) => (
            <Bubble key={index} role={message.role}>
              {message.role === 'user'
                ? <span className="whitespace-pre-wrap">{message.content}</span>
                : <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>}
            </Bubble>
          ))}
          {streaming || chatRequestId ? (
            <Bubble role="assistant">{streaming ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{streaming}</ReactMarkdown> : <span className="animate-pulse text-muted-foreground">…</span>}</Bubble>
          ) : null}
        </>}
        <div ref={endRef} />
      </div>

      <div className="border-t border-border/60 p-3">
        <div className="flex items-end gap-2">
          <Textarea
            rows={2}
            className="min-h-0 flex-1 resize-none text-[13px]"
            placeholder={tab === 'flow' ? 'Message the flow… (Enter to send)' : model ? `Message ${model}…` : 'Select a model first'}
            value={tab === 'flow' ? flowInput : modelInput}
            disabled={!connected || (tab === 'model' && !model)}
            onChange={(event) => (tab === 'flow' ? setFlowInput(event.target.value) : setModelInput(event.target.value))}
            onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void (tab === 'flow' ? sendFlow() : sendModel()); } }}
          />
          {tab === 'flow'
            ? (flowBusy
              ? <Button variant="destructive" size="iconMd" disabled title="Stop via the toolbar Stop button"><Square /></Button>
              : <Button size="iconMd" onClick={() => void sendFlow()} disabled={!connected || !flowInput.trim()}><Play /></Button>)
            : (chatRequestId
              ? <Button variant="destructive" size="iconMd" onClick={() => void request('model.chat.cancel', { request_id: chatRequestId })}><Square /></Button>
              : <Button size="iconMd" onClick={() => void sendModel()} disabled={!connected || !model || !modelInput.trim()}><Play /></Button>)}
        </div>
      </div>
    </aside>
  );
}
