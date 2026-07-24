import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Eraser, Play, Square } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Button } from '../components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Textarea } from '../components/ui/textarea';
import type { GatewayEvent, GatewayResponse } from '../controllers/gateway';

type RequestFn = (method: string, params?: Record<string, unknown>) => Promise<GatewayResponse>;

interface ChatMessage { role: 'user' | 'assistant'; content: string; }
interface ProviderSummary { name: string; models: Array<{ name: string }>; }

const MODEL_KEY = 'agentevolver.playground.model';

/** Direct model chat over model_manager — no agent, no flow: pick a
 * registered model and stream completions through the gateway. */
export default function PlaygroundView({ request, subscribe, sessionId, connected, onNotice }: {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId?: string;
  connected: boolean;
  onNotice: (message: string) => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState(() => localStorage.getItem(MODEL_KEY) ?? '');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState('');
  const [requestId, setRequestId] = useState<string>();
  const requestIdRef = useRef<string>();
  const streamRef = useRef('');
  const endRef = useRef<HTMLDivElement>(null);
  requestIdRef.current = requestId;

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streaming]);
  useEffect(() => { if (model) localStorage.setItem(MODEL_KEY, model); }, [model]);

  useEffect(() => {
    if (!connected) return;
    void (async () => {
      const response = await request('model.list');
      if (response.ok && Array.isArray(response.result.providers)) {
        const names = (response.result.providers as ProviderSummary[]).flatMap((provider) => provider.models.map((item) => item.name));
        setModels(names);
        if (names.length && !names.includes(model)) setModel(names[0]);
      }
    })().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, request]);

  useEffect(() => {
    if (!connected) return;
    return subscribe((event) => {
      const payload = event.payload as Record<string, unknown>;
      if (!event.type.startsWith('model.chat.') || payload.request_id !== requestIdRef.current) return;
      if (event.type === 'model.chat.delta' && typeof payload.text === 'string') {
        streamRef.current += payload.text;
        setStreaming(streamRef.current);
      } else if (event.type === 'model.chat.done') {
        const finalText = typeof payload.message === 'string' && payload.message ? payload.message : streamRef.current;
        setMessages((current) => [...current, { role: 'assistant', content: finalText }]);
        streamRef.current = ''; setStreaming(''); setRequestId(undefined);
      } else if (event.type === 'model.chat.cancelled') {
        if (streamRef.current) setMessages((current) => [...current, { role: 'assistant', content: streamRef.current }]);
        streamRef.current = ''; setStreaming(''); setRequestId(undefined);
      } else if (event.type === 'model.chat.error') {
        onNotice(String(payload.error ?? 'Model call failed'));
        streamRef.current = ''; setStreaming(''); setRequestId(undefined);
      }
    });
  }, [connected, subscribe, onNotice]);

  const send = useCallback(async () => {
    const content = input.trim();
    if (!content || !sessionId || !model || requestId) return;
    const history = [...messages, { role: 'user' as const, content }];
    setMessages(history);
    setInput('');
    streamRef.current = ''; setStreaming('');
    const response = await request('model.chat', { session_id: sessionId, model, messages: history });
    if (!response.ok || typeof response.result.request_id !== 'string') {
      onNotice(response.error?.message ?? 'Could not start the chat');
      return;
    }
    setRequestId(response.result.request_id);
  }, [input, sessionId, model, requestId, messages, request, onNotice]);

  const stop = useCallback(async () => {
    if (requestId) await request('model.chat.cancel', { request_id: requestId });
  }, [requestId, request]);

  const modelOptions = useMemo(() => models.map((name) => (
    <SelectItem key={name} value={name} className="text-xs">{name}</SelectItem>
  )), [models]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border/60 px-5 py-2.5">
        <Select value={model || undefined} onValueChange={setModel}>
          <SelectTrigger className="h-8 w-[320px] text-xs"><SelectValue placeholder="Select a model…" /></SelectTrigger>
          <SelectContent className="max-h-80">{modelOptions}</SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">direct model_manager chat — no agent, no flow</span>
        <div className="ml-auto">
          <Button variant="ghost" size="md" onClick={() => { setMessages([]); streamRef.current = ''; setStreaming(''); }} disabled={!messages.length && !streaming}>
            <Eraser /> Clear
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
          {!messages.length && !streaming ? (
            <p className="mt-16 text-center text-sm text-muted-foreground">
              Pick a model and say something — responses stream straight from model_manager.
            </p>
          ) : null}
          {messages.map((message, index) => (
            <div key={index} className={message.role === 'user' ? 'self-end' : 'self-start'}>
              <div className={message.role === 'user'
                ? 'max-w-xl rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground'
                : 'playground-markdown max-w-2xl rounded-2xl rounded-bl-sm bg-muted px-4 py-2.5 text-sm text-foreground'}>
                {message.role === 'user'
                  ? <span className="whitespace-pre-wrap">{message.content}</span>
                  : <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>}
              </div>
            </div>
          ))}
          {streaming || requestId ? (
            <div className="self-start">
              <div className="playground-markdown max-w-2xl rounded-2xl rounded-bl-sm bg-muted px-4 py-2.5 text-sm text-foreground">
                {streaming ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{streaming}</ReactMarkdown> : <span className="animate-pulse text-muted-foreground">…</span>}
              </div>
            </div>
          ) : null}
          <div ref={endRef} />
        </div>
      </div>

      <div className="border-t border-border/60 px-5 py-3">
        <div className="mx-auto flex w-full max-w-3xl items-end gap-2">
          <Textarea
            rows={2}
            className="min-h-0 flex-1 resize-none text-sm"
            placeholder={model ? `Message ${model}… (Enter to send)` : 'Select a model first'}
            value={input}
            disabled={!connected || !model}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }}
          />
          {requestId
            ? <Button variant="destructive" size="md" onClick={() => void stop()}><Square /> Stop</Button>
            : <Button size="md" onClick={() => void send()} disabled={!connected || !model || !input.trim()}><Play /> Send</Button>}
        </div>
      </div>
    </div>
  );
}
