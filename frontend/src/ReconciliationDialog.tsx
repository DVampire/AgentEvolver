import type { ReconciliationState } from './reconciliation';

export type ReconciliationOutcome = 'applied' | 'not_applied';

/**
 * Fail-closed crash recovery prompt.
 *
 * There is deliberately no dismiss button: closing this question while leaving the task
 * blocked would make the UI look recovered even though the agent can never continue.
 */
export function ReconciliationDialog({ state, onResolve }: {
  state: ReconciliationState;
  onResolve: (callId: string, outcome: ReconciliationOutcome) => void;
}) {
  return (
    <div className="modal-backdrop reconciliation-backdrop" role="presentation">
      <section className="reconciliation-dialog" role="alertdialog" aria-modal="true"
               aria-labelledby="reconciliation-title" aria-describedby="reconciliation-help">
        <header>
          <p className="eyebrow">Crash recovery · confirmation required</p>
          <h2 id="reconciliation-title">Did these actions finish before the interruption?</h2>
          <p id="reconciliation-help">
            AgentEvolver recorded each action before it ran, but the process stopped before
            recording its result. Confirm the real external state. The action will not be
            repeated automatically.
          </p>
        </header>
        <div className="reconciliation-calls">
          {state.calls.map((call) => {
            const busy = state.busyCallId === call.callId;
            return (
              <article className="reconciliation-call" key={call.callId}>
                <div className="reconciliation-call-title">
                  <strong>{call.actionName}</strong>
                  <code>{call.actionType} · {call.callId}</code>
                </div>
                {Object.keys(call.arguments).length ? (
                  <details>
                    <summary>Review recorded arguments</summary>
                    <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
                  </details>
                ) : null}
                <div className="reconciliation-actions">
                  <button className="not-applied" disabled={Boolean(state.busyCallId)}
                          onClick={() => onResolve(call.callId, 'not_applied')}>
                    Did not happen
                  </button>
                  <button className="applied" disabled={Boolean(state.busyCallId)}
                          onClick={() => onResolve(call.callId, 'applied')}>
                    {busy ? 'Recording…' : 'Already happened'}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
        {state.error ? <p className="reconciliation-error" role="alert">{state.error}</p> : null}
        <footer>
          Resolve every item to resume task <code>{state.taskId}</code>.
        </footer>
      </section>
    </div>
  );
}
