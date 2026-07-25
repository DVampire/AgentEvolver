import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, Bot, Check, ChevronDown, Copy, Eraser, Loader2, Pencil, Sparkles, Square, User, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Button } from '../components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Textarea } from '../components/ui/textarea';
import { cn } from '../utils/utils';
import type { GatewayEvent } from '../controllers/gateway';
import type { FrameDoc, RequestFn, RunData } from './types';

// Langflow-style playground: opened from the canvas, its chat drives the
// CURRENT flow (each message = one run on the workflow runtime, with the
// execution record attached to the reply). A second tab chats directly with
// model_manager for quick LLM debugging.
//
// Structure/markup is copied from Langflow's IOModal chatView + the newer
// playgroundComponent chat-input: a centered max-w-[768px] column, a bordered
// focus-reactive composer card with an auto-resizing textarea over a toolbar
// row, an ArrowUp send button, per-message avatar rows with a hover copy bar,
// and a branded "New chat" empty state.

interface FlowInputField { name: string; input_type: string; required: boolean; default: string; }
interface ExecutionStep { step: string; state: string; duration: string; }
interface FlowMessage { role: 'user' | 'assistant'; content: string; failed?: boolean; execution?: ExecutionStep[]; duration?: string; }
interface ModelMessage { role: 'user' | 'assistant'; content: string; }

const PREFERRED_INPUT_NAMES = ['message', 'input', 'question', 'task', 'query', 'prompt', 'text'];
const MODEL_KEY = 'agentevolver.playground.model';

// Langflow chat-input auto-resize bounds (constants/constants.ts).
const CHAT_INPUT_MIN_HEIGHT = 24;
const CHAT_INPUT_MAX_HEIGHT = 200;

// Copied from Langflow's text-area-wrapper.tsx resizeTextarea.
function resizeTextarea(textarea: HTMLTextAreaElement, value: string): void {
  textarea.style.height = '0px';
  const scrollHeight = textarea.scrollHeight;
  if (!value || value.trim() === '') {
    textarea.style.height = `${CHAT_INPUT_MIN_HEIGHT}px`;
    textarea.style.overflowY = 'hidden';
  } else {
    const newHeight = Math.max(CHAT_INPUT_MIN_HEIGHT, Math.min(scrollHeight, CHAT_INPUT_MAX_HEIGHT));
    textarea.style.height = `${newHeight}px`;
    textarea.style.overflowY = scrollHeight > CHAT_INPUT_MAX_HEIGHT ? 'auto' : 'hidden';
  }
}

function frameDuration(frame: { started_at?: string | null; finished_at?: string | null }): string {
  if (!frame.started_at || !frame.finished_at) return '';
  const ms = new Date(frame.finished_at).getTime() - new Date(frame.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

// Wall duration across a run's frames (min start → max finish), for the
// "Finished in Xs" metadata line.
function runWallDuration(frames: Record<string, FrameDoc>): string {
  let start = Infinity;
  let finish = -Infinity;
  for (const frame of Object.values(frames)) {
    if (frame.started_at) start = Math.min(start, new Date(frame.started_at).getTime());
    if (frame.finished_at) finish = Math.max(finish, new Date(frame.finished_at).getTime());
  }
  if (!Number.isFinite(start) || !Number.isFinite(finish) || finish < start) return '';
  const ms = finish - start;
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

// Langflow message-options.tsx: the floating bordered action bar — an optional
// edit (pencil) for user messages, plus copy with a copied-state check.
function MessageActions({ copyText, onEdit }: { copyText?: string; onEdit?: () => void }) {
  const [copied, setCopied] = useState(false);
  if (!copyText && !onEdit) return null;
  return (
    <div className="flex items-center rounded-md border border-border bg-background">
      {onEdit ? (
        <div className="p-1">
          <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Edit message" onClick={onEdit}>
            <Pencil className="h-4 w-4" />
          </Button>
        </div>
      ) : null}
      {copyText ? (
        <div className="p-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            aria-label={copied ? 'Copied' : 'Copy message'}
            onClick={() => { void navigator.clipboard.writeText(copyText); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
          >
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

// Langflow chatMessage/chat-message.tsx row: a full-width row with a 32px
// avatar, sender name (+ inline metadata), then the message content — not
// left/right bubbles. A hover copy bar floats above the row.
function Bubble({ role, failed, senderName, metadata, copyText, onEdit, children }: {
  role: 'user' | 'assistant';
  failed?: boolean;
  senderName: string;
  metadata?: React.ReactNode;
  copyText?: string;
  onEdit?: () => void;
  children: React.ReactNode;
}) {
  const isUser = role === 'user';
  return (
    <div className="w-full py-4 word-break-break-word">
      <div className="group relative flex w-full gap-4 rounded-md p-2 hover:bg-muted">
        {(copyText || onEdit) ? (
          <div className="invisible absolute bottom-full right-0 group-hover:visible">
            <MessageActions copyText={copyText} onEdit={onEdit} />
          </div>
        ) : null}
        <div className={cn(
          'relative flex h-[32px] w-[32px] items-center justify-center overflow-hidden rounded-md text-2xl',
          isUser ? 'border border-border hover:border-input' : 'bg-muted',
        )}>
          <div className="flex h-[18px] w-[18px] items-center justify-center">
            {isUser ? <User className="h-[18px] w-[18px]" /> : <Bot className="h-[18px] w-[18px]" />}
          </div>
        </div>
        <div className="flex w-[94%] flex-col">
          <div className="flex w-full items-baseline gap-3 pb-2 text-sm font-semibold">
            <span className="flex items-center gap-2">{senderName}</span>
            {metadata}
          </div>
          <div className={cn('playground-markdown min-w-0 text-sm leading-relaxed', failed ? 'text-destructive' : 'text-foreground')}>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

// Langflow chat-view.tsx branded empty state (LangflowLogo → our brand glyph).
function EmptyState({ subtitle, hint }: { subtitle: string; hint?: React.ReactNode }) {
  return (
    <div className="flex flex-grow w-full flex-col items-center justify-center">
      <div className="flex flex-col items-center justify-center gap-4 p-8">
        <Sparkles className="h-10 w-10 scale-[1.5] text-primary" aria-hidden="true" />
        <div className="flex flex-col items-center justify-center">
          <h3 className="mt-2 pb-2 text-2xl font-semibold text-primary">New chat</h3>
          <p className="text-lg text-muted-foreground">{subtitle}</p>
          {hint ? <p className="mt-1 text-center text-xs text-muted-foreground">{hint}</p> : null}
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Stick-to-bottom: only auto-scroll when the user is already near the bottom
  // (Langflow's use-stick-to-bottom behavior — don't yank them down mid-scroll).
  const [atBottom, setAtBottom] = useState(true);
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  }, []);

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

  // Run a flow that has no Chat Input directly (Langflow's no-input.tsx).
  const runFlowNoInput = useCallback(async () => {
    if (pendingRun || runId) return;
    const input: Record<string, unknown> = {};
    for (const field of inputNodes) {
      if (field.default) {
        try { input[field.name] = ['array', 'object', 'number', 'boolean'].includes(field.input_type) ? JSON.parse(field.default) : field.default; }
        catch { input[field.name] = field.default; }
      }
    }
    const rid = await startRun(input);
    if (!rid) {
      setFlowMessages((current) => [...current, { role: 'assistant', content: 'The flow could not start — check the notice.', failed: true }]);
      return;
    }
    setPendingRun(rid);
  }, [pendingRun, runId, inputNodes, startRun]);

  // When the watched run settles, turn its outputs + frames into a reply.
  useEffect(() => {
    if (!pendingRun || runId) return;
    const frames = runData?.frames ?? {};
    const execution: ExecutionStep[] = Object.values(frames).map((frame: FrameDoc) => ({
      step: frame.step_id + (frame.item_index != null ? `[${frame.item_index}]` : '') + (frame.iteration != null ? ` r${frame.iteration}` : ''),
      state: frame.state,
      duration: frameDuration(frame),
    }));
    const duration = runWallDuration(frames);
    setFlowMessages((current) => [...current, runError
      ? { role: 'assistant', content: runError, failed: true, execution }
      : { role: 'assistant', content: formatOutputs(runOutput), execution, duration }]);
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
  useEffect(() => { if (atBottom) endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [flowMessages, modelMessages, streaming, tab, pendingRun, atBottom]);

  // Auto-resize the composer textarea on value / tab change (Langflow parity).
  const currentValue = tab === 'flow' ? flowInput : modelInput;
  useEffect(() => { if (inputRef.current) resizeTextarea(inputRef.current, currentValue); }, [currentValue, tab]);

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
  const busy = tab === 'flow' ? flowBusy : Boolean(chatRequestId);
  const value = tab === 'flow' ? flowInput : modelInput;
  const inputDisabled = !connected || (tab === 'model' && !model);
  const canSend = tab === 'flow' ? Boolean(flowInput.trim()) && !flowBusy : Boolean(model && modelInput.trim());

  const onSend = () => void (tab === 'flow' ? sendFlow() : sendModel());
  const onStop = () => { if (tab === 'model' && chatRequestId) void request('model.chat.cancel', { request_id: chatRequestId }); };

  const placeholder = tab === 'flow'
    ? 'Send a message...'
    : model ? `Message ${model}…` : 'Select a model first';

  const hasFlow = flowMessages.length > 0 || flowBusy;
  const hasModel = modelMessages.length > 0 || Boolean(streaming) || Boolean(chatRequestId);

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

      {/* Transcript — centered max-w-[768px] column (Langflow chat-view.tsx) */}
      <div ref={scrollRef} onScroll={onScroll} className="relative flex min-h-0 flex-1 flex-col overflow-y-auto px-4">
        {!atBottom ? (
          <button
            type="button"
            onClick={() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); setAtBottom(true); }}
            className="absolute bottom-3 left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-md hover:text-foreground"
            aria-label="Scroll to bottom"
          >
            <ChevronDown className="h-4 w-4" />
          </button>
        ) : null}
        <div className="mx-auto flex w-5/6 max-w-[768px] flex-grow flex-col">
          {tab === 'flow' ? (
            hasFlow ? <>
              {flowMessages.map((message, index) => (
                <Bubble
                  key={index}
                  role={message.role}
                  failed={message.failed}
                  senderName={message.role === 'user' ? 'User' : 'AI'}
                  copyText={message.content}
                  onEdit={message.role === 'user' && !flowBusy ? () => { setFlowInput(message.content); setFlowMessages((current) => current.slice(0, index)); inputRef.current?.focus(); } : undefined}
                  metadata={message.role === 'assistant' && message.duration ? (
                    <span className="flex items-center gap-1.5 text-sm font-normal text-muted-foreground">
                      <Check className="h-4 w-4 text-accent-emerald-foreground" />
                      Finished in {message.duration}
                    </span>
                  ) : undefined}
                >
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
              {flowBusy ? (
                <Bubble role="assistant" senderName="AI">
                  <span className="lf-shimmer text-sm font-medium">Flow running…</span>
                </Bubble>
              ) : null}
            </> : (
              <EmptyState
                subtitle="Test your flow with a chat prompt"
                hint={targetInput
                  ? <>Your message is passed as <code>${`{inputs.${targetInput.name}}`}</code></>
                  : 'Add a string Flow Input to feed your message into the flow.'}
              />
            )
          ) : (
            hasModel ? <>
              {modelMessages.map((message, index) => (
                <Bubble key={index} role={message.role} senderName={message.role === 'user' ? 'User' : 'AI'} copyText={message.content}
                  onEdit={message.role === 'user' && !chatRequestId ? () => { setModelInput(message.content); setModelMessages((current) => current.slice(0, index)); inputRef.current?.focus(); } : undefined}>
                  {message.role === 'user'
                    ? <span className="whitespace-pre-wrap">{message.content}</span>
                    : <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>}
                </Bubble>
              ))}
              {streaming || chatRequestId ? (
                <Bubble role="assistant" senderName="AI">
                  {streaming ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{streaming}</ReactMarkdown> : <span className="animate-pulse text-muted-foreground">…</span>}
                </Bubble>
              ) : null}
            </> : (
              <EmptyState subtitle="Chat directly with a model" hint="No agent, no flow — straight to model_manager." />
            )
          )}
          <div ref={endRef} />
        </div>
      </div>

      {/* No Chat Input → a "Run Flow" block instead of the composer (Langflow no-input.tsx) */}
      {tab === 'flow' && !targetInput ? (
        <div className="mx-auto w-full max-w-[768px] px-4 pb-4 md:w-5/6">
          <div className="flex w-full flex-col items-center justify-center gap-3 rounded-md border border-input bg-muted p-2 py-4">
            {!flowBusy ? (
              <Button className="font-semibold" onClick={() => void runFlowNoInput()} disabled={!connected}>Run Flow</Button>
            ) : (
              <Button unstyled disabled className="cursor-default rounded-md bg-muted px-2.5 py-1.5 text-foreground">
                <div className="flex items-center gap-2 text-sm font-medium">Running<Loader2 className="h-4 w-4 animate-spin" /></div>
              </Button>
            )}
            <p className="text-sm text-muted-foreground">This flow has no Chat Input — run it directly, or add a Chat Input node to send messages.</p>
          </div>
        </div>
      ) : (
      /* Composer — bordered focus-reactive card (Langflow input-wrapper.tsx) */
      <div className="mx-auto w-full max-w-[768px] px-4 pb-4 md:w-5/6">
        <div
          data-testid="input-wrapper"
          className="flex w-full cursor-text flex-col rounded-md border border-input bg-muted p-3 hover:border-muted-foreground focus-within:border-primary"
          onClick={(event) => {
            const target = event.target as HTMLElement;
            if (target.closest("textarea,button,input,[role='button']")) return;
            inputRef.current?.focus();
          }}
        >
          <div className="w-full">
            <Textarea
              ref={inputRef}
              rows={1}
              data-testid="input-chat-playground"
              className="form-input custom-scroll !min-h-0 block w-full resize-none rounded-none border-0 !bg-transparent p-0 shadow-none focus:border-ring focus:ring-0 sm:text-sm"
              style={{ maxHeight: `${CHAT_INPUT_MAX_HEIGHT}px` }}
              placeholder={placeholder}
              value={value}
              disabled={inputDisabled}
              onChange={(event) => {
                if (tab === 'flow') setFlowInput(event.target.value); else setModelInput(event.target.value);
                resizeTextarea(event.target, event.target.value);
              }}
              onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); onSend(); } }}
            />
          </div>
          <div className="flex w-full items-center justify-between pt-3">
            <div className="flex-shrink-0" />
            <div className="flex flex-shrink-0 items-center gap-2">
              <Button
                unstyled
                className={cn(
                  'flex h-6 w-6 items-center justify-center rounded-md px-0',
                  'bg-primary text-primary-foreground hover:bg-primary-hover hover:text-secondary',
                  !busy && !canSend && 'pointer-events-none opacity-50',
                )}
                onClick={busy ? onStop : onSend}
                disabled={inputDisabled}
                data-testid={busy ? 'button-stop' : 'button-send'}
                aria-label={busy ? 'Stop' : 'Send'}
                title={busy ? 'Stop' : 'Send'}
              >
                <div className="flex h-fit w-fit items-center gap-2 text-sm font-medium">
                  {busy ? <Square className="h-3.5 w-3.5" fill="currentColor" aria-hidden /> : <ArrowUp className="h-4 w-4" />}
                </div>
              </Button>
            </div>
          </div>
        </div>
      </div>
      )}
    </aside>
  );
}
