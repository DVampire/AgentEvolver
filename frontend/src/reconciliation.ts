import type { GatewayEvent } from './protocol/gateway';

/** A possibly-applied external effect whose process died before its receipt was durable. */
export interface UnsettledCall {
  callId: string;
  actionType: string;
  actionName: string;
  arguments: Record<string, unknown>;
}

/** The small, client-safe projection of a durable execution checkpoint. */
export interface ReconciliationState {
  taskId: string;
  sessionId?: string;
  calls: UnsettledCall[];
  busyCallId?: string;
  error?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/** Parse an untrusted wire checkpoint without letting malformed recovery data crash the UI. */
export function reconciliationFromCheckpoint(
  checkpoint: unknown,
  taskId: string,
  sessionId?: string,
): ReconciliationState | undefined {
  if (!isRecord(checkpoint) || !Array.isArray(checkpoint.unsettled_calls)) return undefined;
  const calls = checkpoint.unsettled_calls.flatMap((value): UnsettledCall[] => {
    if (!isRecord(value) || typeof value.call_id !== 'string' || !value.call_id) return [];
    return [{
      callId: value.call_id,
      actionType: typeof value.action_type === 'string' ? value.action_type : 'action',
      actionName: typeof value.action_name === 'string' ? value.action_name : 'unknown action',
      arguments: isRecord(value.arguments) ? value.arguments : {},
    }];
  });
  if (!calls.length) return undefined;
  const checkpointTaskId = typeof checkpoint.task_id === 'string' ? checkpoint.task_id : '';
  const resolvedTaskId = taskId || checkpointTaskId;
  return resolvedTaskId ? { taskId: resolvedTaskId, sessionId, calls } : undefined;
}

/** Convert the Gateway's recovery event into dialog state shared by browser and CLI. */
export function reconciliationFromEvent(event: GatewayEvent): ReconciliationState | undefined {
  if (event.type !== 'task.reconciliation.required') return undefined;
  return reconciliationFromCheckpoint(
    event.payload.checkpoint,
    event.task_id ?? '',
    event.session_id,
  );
}
