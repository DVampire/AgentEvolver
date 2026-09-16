import { useCallback, useEffect, useRef, useState } from 'react';
import { ClipboardList, Loader2 } from 'lucide-react';

import type { RequestFn } from '../canvas/types';
import type { GatewayEvent } from '../controllers/gateway';

/* ===========================================================================
   Plan mode, against the composer.

   It belongs here and not at the top of the column because of what it does:
   while it is on, the next thing you send gets read, searched and reasoned
   about, and nothing that changes state runs until you approve the plan. That
   is a fact about the send button, so it is drawn next to the send button. At
   the top of the column you would not be looking at it while typing.

   `plan.mode.changed` is published whenever `plan.set` runs, so two tabs and
   the agent's own `exit_plan_mode` all land here without anybody polling.
   =========================================================================== */

/** `PlanState.summary()` — see agentevolver/plan/types.py. */
interface PlanSummary {
  session_id: string;
  active: boolean;
  /** The plan a person approved, verbatim; empty when plan mode was merely
   *  called off. `leave()` records no approval, on purpose. */
  approved_plan: string;
  entered_at: string | null;
  approved_at: string | null;
}

/** A summary, or undefined for anything that is not one.
 *
 * `active` is the field the bar is about, so a response without a boolean one
 * is not a plan state — and a bar that has to guess at its own control's
 * position should not be drawn at all. */
function readPlan(raw: unknown): PlanSummary | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const value = raw as Record<string, unknown>;
  if (typeof value.active !== 'boolean') return undefined;
  return {
    session_id: String(value.session_id ?? ''),
    active: value.active,
    approved_plan: typeof value.approved_plan === 'string' ? value.approved_plan : '',
    entered_at: typeof value.entered_at === 'string' ? value.entered_at : null,
    approved_at: typeof value.approved_at === 'string' ? value.approved_at : null,
  };
}

export function PlanBar({ request, subscribe, sessionId }: {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId: string;
}) {
  const [state, setState] = useState<PlanSummary>();
  const [pending, setPending] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  // Undefined until the gateway answers, and back to undefined if it cannot:
  // no bar at all is the correct degradation for a control whose state we do
  // not know. Claiming "plan mode: off" without having read it would be worse.
  const load = useCallback(() => {
    void (async () => {
      try {
        const response = await request('plan.get', { session_id: sessionId });
        if (!alive.current) return;
        setState(response.ok ? readPlan(response.result) : undefined);
      } catch {
        if (alive.current) setState(undefined);
      }
    })();
  }, [request, sessionId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => subscribe((event) => {
    if (event.type !== 'plan.mode.changed') return;
    if (event.session_id !== sessionId) return;
    // The envelope's payload IS the summary — `plan.set` publishes it flat.
    setState((current) => readPlan(event.payload) ?? current);
  }), [subscribe, sessionId]);

  const toggle = useCallback((active: boolean) => {
    setPending(true);
    void (async () => {
      try {
        const response = await request('plan.set', { session_id: sessionId, active });
        if (alive.current && response.ok) setState((current) => readPlan(response.result) ?? current);
      } catch {
        // The published `plan.mode.changed` is the other way this arrives; a
        // failed toggle simply leaves the bar as it was.
      } finally {
        if (alive.current) setPending(false);
      }
    })();
  }, [request, sessionId]);

  if (!state) return null;

  if (!state.active) {
    return (
      <div className="science-plan">
        <button className="science-plan-chip" disabled={pending} onClick={() => toggle(true)}>
          {pending ? <Loader2 className="science-spinner inline" /> : <ClipboardList size={12} />}
          Plan mode
        </button>
        {state.approved_plan
          ? <span className="science-plan-note">a plan was approved for this run</span>
          : null}
      </div>
    );
  }

  return (
    <div className="science-plan on">
      <span className="science-plan-icon"><ClipboardList size={13} /></span>
      <strong>Plan mode</strong>
      <span className="science-plan-note">
        The agent reads, searches and reasons — nothing that changes state runs until you approve its plan.
      </span>
      <button className="science-plan-off" disabled={pending} onClick={() => toggle(false)}>
        {pending ? <Loader2 className="science-spinner inline" /> : null}
        Turn off
      </button>
    </div>
  );
}
