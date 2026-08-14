import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleCheck, CirclePause, Target, TriangleAlert } from 'lucide-react';

import type { RequestFn } from '../canvas/types';
import type { GatewayEvent } from '../controllers/gateway';

/* ===========================================================================
   The goal: what this project is working toward, above everything it said.

   Not a message. A message records what happened at one instant and then
   scrolls away; the objective is true for the whole conversation, and a
   conversation whose heading has scrolled off the top is a conversation you
   have to reconstruct. So it sits above the thread, outside the scroll, where
   it reads as the heading the transcript sits under.

   One goal per session by construction — `goal_manager.current` returns the
   single open one — so this is a card and not a list.
   =========================================================================== */

/** `_command_goal_get`'s `goal`, see gateway/service.py. `null` when a person
 *  never set one, which is deliberately different from an empty objective. */
interface Goal {
  objective: string;
  /** GoalPhase: active | paused | blocked | complete. */
  phase: string;
  revision: number;
  blocked_reason: string | null;
  summary: string;
}

export function GoalCard({ request, subscribe, sessionId }: {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId: string;
}) {
  const [goal, setGoal] = useState<Goal | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  // Any failure — no such command, no session, a gateway mid-restart — leaves
  // `goal` null and this renders nothing. An error banner pinned over the
  // conversation would cost more than the surface is worth.
  const load = useCallback(() => {
    void (async () => {
      try {
        const response = await request('goal.get', { session_id: sessionId });
        if (!alive.current) return;
        if (!response.ok) { setGoal(null); return; }
        const result = (response.result ?? {}) as { goal?: Goal | null };
        setGoal(result.goal && result.goal.objective ? result.goal : null);
      } catch {
        if (alive.current) setGoal(null);
      }
    })();
  }, [request, sessionId]);

  useEffect(() => { load(); }, [load]);

  // Nothing on the wire announces a goal change, and only the agent and the
  // person change one — so the run boundaries are where a re-read can pay off.
  useEffect(() => subscribe((event) => {
    if (event.session_id !== sessionId) return;
    if (event.type === 'task.started' || event.type === 'task.completed'
      || event.type === 'task.failed' || event.type === 'task.cancelled') load();
  }), [subscribe, sessionId, load]);

  if (!goal) return null;
  const phase = goal.phase || 'active';
  return (
    <section className={`science-goal ${phase}`}>
      <span className="science-goal-icon"><PhaseIcon phase={phase} /></span>
      <div className="science-goal-body">
        <span className="science-goal-eyebrow">Goal<i>rev {goal.revision}</i></span>
        <p>{goal.objective}</p>
        {phase === 'blocked' && goal.blocked_reason
          ? <em className="science-goal-blocked">Blocked on {goal.blocked_reason}</em>
          : null}
      </div>
      <span className={`science-goal-phase ${phase}`}>{phase}</span>
    </section>
  );
}

function PhaseIcon({ phase }: { phase: string }) {
  if (phase === 'blocked') return <TriangleAlert size={13} />;
  if (phase === 'paused') return <CirclePause size={13} />;
  if (phase === 'complete') return <CircleCheck size={13} />;
  return <Target size={13} />;
}
