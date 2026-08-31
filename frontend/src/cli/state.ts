import type { GatewayEvent } from './protocol.js';
import {
  reconciliationFromEvent,
  type ReconciliationState,
} from '../reconciliation.js';

export type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error';
export type TimelineType = 'user' | 'agent' | 'tool' | 'system' | 'error';

export interface TimelineEntry {
  id: string;
  type: TimelineType;
  title: string;
  body?: string;
  timestamp: string;
  pending?: boolean;
}

export interface ApprovalState {
  id: string;
  summary: string;
  sessionId?: string;
}

export interface AppState {
  connection: ConnectionState;
  sessionId?: string;
  activeTaskId?: string;
  entries: TimelineEntry[];
  approval?: ApprovalState;
  reconciliation?: ReconciliationState;
  notice?: string;
}

export type AppAction =
  | { type: 'connection'; value: ConnectionState }
  | { type: 'session'; sessionId: string }
  | { type: 'task'; taskId?: string }
  | { type: 'approval.clear' }
  | { type: 'reconciliation.update'; value: ReconciliationState }
  | { type: 'reconciliation.clear' }
  | { type: 'notice'; value?: string }
  | { type: 'event'; event: GatewayEvent };

export const initialState: AppState = {
  connection: 'connecting',
  entries: [],
};

export function appReducer(state: AppState, action: AppAction): AppState {
  if (action.type === 'connection') return { ...state, connection: action.value };
  if (action.type === 'session') return { ...state, sessionId: action.sessionId };
  if (action.type === 'task') return { ...state, activeTaskId: action.taskId };
  if (action.type === 'approval.clear') return { ...state, approval: undefined };
  if (action.type === 'reconciliation.update') return { ...state, reconciliation: action.value };
  if (action.type === 'reconciliation.clear') return { ...state, reconciliation: undefined };
  if (action.type === 'notice') return { ...state, notice: action.value };

  const event = action.event;
  if (event.type === 'gateway.connection') {
    const status = String(event.payload.status ?? 'disconnected') as ConnectionState;
    return { ...state, connection: status };
  }
  if (event.type === 'approval.requested') {
    return {
      ...state,
      approval: {
        id: String(event.payload.approval_id ?? event.task_id ?? 'approval'),
        summary: String(event.payload.summary ?? 'Agent requests approval'),
        sessionId: event.session_id,
      },
    };
  }
  if (['approval.responded', 'approval.expired', 'approval.cancelled'].includes(event.type)) {
    const approvalId = String(event.payload.approval_id ?? '');
    return !state.approval || state.approval.id === approvalId
      ? { ...state, approval: undefined }
      : state;
  }
  if (event.type === 'task.reconciliation.required') {
    const reconciliation = reconciliationFromEvent(event);
    const entry = eventToEntry(event);
    return reconciliation
      ? {
          ...state,
          activeTaskId: reconciliation.taskId,
          reconciliation,
          entries: entry ? [...state.entries.slice(-500), entry] : state.entries,
        }
      : state;
  }
  if (event.type === 'task.reconciliation.completed') {
    const entry = eventToEntry(event);
    return {
      ...state,
      reconciliation: undefined,
      entries: entry ? [...state.entries.slice(-500), entry] : state.entries,
    };
  }
  const entry = eventToEntry(event);
  if (!entry) return state;
  const activeTaskId = ['task.completed', 'task.failed', 'task.cancelled'].includes(event.type)
    ? undefined
    : state.activeTaskId;
  const reconciliation = ['task.completed', 'task.failed', 'task.cancelled'].includes(event.type)
    ? undefined
    : state.reconciliation;
  return { ...state, activeTaskId, reconciliation, entries: [...state.entries.slice(-500), entry] };
}

function eventToEntry(event: GatewayEvent): TimelineEntry | undefined {
  const base = { id: `${event.session_id ?? 'gateway'}:${event.seq_no}:${event.type}`, timestamp: event.timestamp };
  if (event.type === 'task.submitted') {
    return { ...base, type: 'user', title: 'You', body: String(event.payload.content ?? '') };
  }
  if (event.type === 'task.completed') {
    return { ...base, type: 'agent', title: 'AgentEvolver', body: String(event.payload.message ?? event.payload.result ?? 'Task completed') };
  }
  if (event.type === 'task.failed') {
    return { ...base, type: 'error', title: 'Task failed', body: String(event.payload.error ?? 'Unknown error') };
  }
  if (event.type === 'task.cancelled') return { ...base, type: 'system', title: 'Task cancelled' };
  if (event.type === 'task.reconciliation.required') {
    return { ...base, type: 'system', title: 'Crash recovery needs confirmation', body: String(event.payload.unsettled ?? '') };
  }
  if (event.type === 'task.reconciliation.completed') {
    return { ...base, type: 'system', title: 'Recovery confirmed', body: 'The interrupted task is resuming.' };
  }
  if (event.type === 'gateway.log') return { ...base, type: 'system', title: 'Gateway', body: String(event.payload.message ?? '') };
  if (event.type !== 'trace.event') return undefined;

  const trace = event.payload;
  const traceType = String(trace.event_type ?? 'event');
  const title = String(trace.agent_name ?? trace.action_name ?? trace.label ?? 'Agent');
  const body = trace.message ?? trace.error ?? (trace.input ? JSON.stringify(trace.input) : undefined);
  const entryType: TimelineType = traceType.startsWith('tool') || traceType.startsWith('skill') ? 'tool' : 'agent';
  return { ...base, type: entryType, title: `${title} · ${traceType}`, body: body ? String(body) : undefined, pending: traceType.endsWith('start') };
}
