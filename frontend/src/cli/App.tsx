import React, { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { Box, Text, useApp, useInput } from 'ink';

import { ApprovalDialog } from './components/ApprovalDialog.js';
import { Composer } from './components/Composer.js';
import { Header } from './components/Header.js';
import { ReconciliationDialog } from './components/ReconciliationDialog.js';
import { StatusBar } from './components/StatusBar.js';
import { Transcript } from './components/Transcript.js';
import { createGatewayClient, type GatewayClientOptions } from './gateway/index.js';
import type { GatewayEvent } from './protocol.js';
import { appReducer, initialState } from './state.js';
import { reconciliationFromCheckpoint } from '../reconciliation.js';

export function App({ options }: { options: GatewayClientOptions }) {
  const { exit } = useApp();
  const [state, dispatch] = useReducer(appReducer, initialState);
  const [input, setInput] = useState('');
  const clientRef = useRef<ReturnType<typeof createGatewayClient> | undefined>(undefined);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const client = createGatewayClient(options);
    clientRef.current = client;
    const unsubscribe = client.onEvent((event) => dispatch({ type: 'event', event }));
    void (async () => {
      try {
        await client.start();
        const hello = await client.request('hello');
        if (!hello.ok) throw new Error(hello.error?.message ?? 'Gateway handshake failed');
        // Reopen the most recently used project, matching the browser. Creating a fresh
        // session on every CLI start hid the durable conversation that contains a crash
        // reconciliation request, leaving a safely-blocked task with no visible way out.
        const listed = await client.request('session.list');
        if (!listed.ok) throw new Error(listed.error?.message ?? 'Could not list sessions');
        const sessions = Array.isArray(listed.result.sessions) ? listed.result.sessions : [];
        const recent = sessions.find((item) => (
          item && typeof item === 'object'
          && (item as Record<string, unknown>).has_work !== false
          && typeof (item as Record<string, unknown>).session_id === 'string'
        )) as Record<string, unknown> | undefined;
        let currentSessionId = typeof recent?.session_id === 'string' ? recent.session_id : '';
        if (!currentSessionId) {
          const session = await client.request('session.create', {
            workspace: options.workspace ?? process.cwd(),
            name: 'terminal',
          });
          if (!session.ok || typeof session.result.session_id !== 'string') {
            throw new Error(session.error?.message ?? 'Could not create session');
          }
          currentSessionId = session.result.session_id;
        }
        dispatch({ type: 'session', sessionId: currentSessionId });

        // Session event buffers are process-local. The conversation transcript is the
        // durable stream, so replay it to recover prompts emitted before this CLI began.
        const conversations = await client.request('conversation.list', {
          session_id: currentSessionId,
          view: 'chat',
        });
        const items = conversations.ok && Array.isArray(conversations.result.conversations)
          ? conversations.result.conversations : [];
        const latest = items[0];
        if (latest && typeof latest === 'object'
            && typeof (latest as Record<string, unknown>).conversation_id === 'string') {
          const history = await client.request('conversation.events', {
            session_id: currentSessionId,
            conversation_id: (latest as Record<string, unknown>).conversation_id,
          });
          if (history.ok && Array.isArray(history.result.events)) {
            for (const event of history.result.events) {
              if (event && typeof event === 'object') {
                dispatch({ type: 'event', event: event as GatewayEvent });
              }
            }
          }
        }
        dispatch({ type: 'connection', value: 'connected' });
      } catch (error) {
        dispatch({ type: 'connection', value: 'error' });
        dispatch({ type: 'notice', value: error instanceof Error ? error.message : String(error) });
      }
    })();
    return () => {
      unsubscribe();
      void client.close();
    };
  }, [options.configPath, options.connectUrl, options.token, options.workspace]);

  const submit = useCallback(async () => {
    const content = input.trim();
    const sessionId = stateRef.current.sessionId;
    if (!content || !sessionId || stateRef.current.activeTaskId) return;
    setInput('');
    try {
      const response = await clientRef.current?.request('task.submit', { session_id: sessionId, content });
      if (!response?.ok || typeof response.result.task_id !== 'string') throw new Error(response?.error?.message ?? 'Task submission failed');
      dispatch({ type: 'task', taskId: response.result.task_id });
    } catch (error) {
      dispatch({ type: 'notice', value: error instanceof Error ? error.message : String(error) });
    }
  }, [input]);

  const respondToApproval = useCallback(async (decision: 'allow_once' | 'reject') => {
    const approval = stateRef.current.approval;
    if (!approval) return;
    try {
      const response = await clientRef.current?.request('approval.respond', {
        approval_id: approval.id,
        session_id: approval.sessionId,
        decision,
      });
      if (!response?.ok) throw new Error(response?.error?.message ?? 'Approval response failed');
      // `delivered: false` means another tab or the timeout settled it first. Either way
      // this dialog is stale and must not keep claiming the Tool is waiting.
      dispatch({ type: 'approval.clear' });
    } catch (error) {
      dispatch({ type: 'notice', value: error instanceof Error ? error.message : String(error) });
    }
  }, []);

  const respondToReconciliation = useCallback(async (outcome: 'applied' | 'not_applied') => {
    const reconciliation = stateRef.current.reconciliation;
    const call = reconciliation?.calls[0];
    if (!reconciliation || !call || reconciliation.busyCallId) return;
    dispatch({
      type: 'reconciliation.update',
      value: { ...reconciliation, busyCallId: call.callId, error: undefined },
    });
    try {
      const response = await clientRef.current?.request('execution.reconcile', {
        session_id: reconciliation.sessionId,
        task_id: reconciliation.taskId,
        call_id: call.callId,
        outcome,
      });
      if (!response?.ok) throw new Error(response?.error?.message ?? 'Recovery response failed');
      if (response.result.resumed === true) {
        dispatch({ type: 'reconciliation.clear' });
        return;
      }
      const updated = reconciliationFromCheckpoint(
        response.result.checkpoint,
        reconciliation.taskId,
        reconciliation.sessionId,
      );
      if (!updated) throw new Error('Gateway returned an invalid recovery checkpoint');
      dispatch({ type: 'reconciliation.update', value: updated });
    } catch (error) {
      dispatch({
        type: 'reconciliation.update',
        value: {
          ...reconciliation,
          error: error instanceof Error ? error.message : String(error),
        },
      });
    }
  }, []);

  useInput((character, key) => {
    if (key.ctrl && character === 'c') {
      const taskId = stateRef.current.activeTaskId;
      if (taskId) {
        void clientRef.current?.request('task.cancel', { task_id: taskId });
      } else {
        exit();
      }
      return;
    }
    if (stateRef.current.reconciliation && character === 'y') {
      void respondToReconciliation('applied');
      return;
    }
    if (stateRef.current.reconciliation && character === 'n') {
      void respondToReconciliation('not_applied');
      return;
    }
    if (character === 'q' && !input && !stateRef.current.activeTaskId) {
      exit();
      return;
    }
    if (stateRef.current.approval && character === 'a') {
      void respondToApproval('allow_once');
      return;
    }
    if (stateRef.current.approval && character === 'r') {
      void respondToApproval('reject');
      return;
    }
    if (key.return) {
      void submit();
      return;
    }
    if (key.backspace || key.delete) {
      setInput((value) => value.slice(0, -1));
      return;
    }
    if (!key.ctrl && !key.meta && character) setInput((value) => value + character);
  });

  return (
    <Box flexDirection="column">
      <Header connection={state.connection} sessionId={state.sessionId} remote={Boolean(options.connectUrl)} />
      <Transcript entries={state.entries} />
      {state.approval ? <ApprovalDialog approval={state.approval} /> : null}
      {state.reconciliation ? <ReconciliationDialog reconciliation={state.reconciliation} /> : null}
      <Composer value={input} disabled={!state.sessionId || Boolean(state.activeTaskId) || Boolean(state.reconciliation)} />
      <StatusBar taskId={state.activeTaskId} notice={state.notice} />
      {state.connection === 'error' ? <Text color="red">Gateway connection failed. Check Python dependencies and configuration.</Text> : null}
    </Box>
  );
}
