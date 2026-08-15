# The log reaches disk before a mutation, and nowhere else

Status: current

## Problem

`trace_manager.emit` queues an event and returns; a background writer drains the queue.
That is deliberate — recording must not sit on the hot path — and it means the log trails
the run by however far behind the writer is.

The lag is free until the process dies inside it. A run killed between "the agent decided
to run this command" and "the writer caught up" leaves no record of the command at all, and
afterwards the question "did the destructive one execute?" has no answer anywhere. The
filesystem shows a state; nothing says whether the agent caused it. That is the worst shape
a missing record can take: not a gap in a transcript, but a question that can no longer be
asked of the system.

Sessions end this way routinely — a killed container, an interrupted benchmark, an operator
stopping a run that looked wrong.

## Decision

`Agent._checkpoint_before_effects` awaits `trace_manager.flush()` immediately before
dispatching a tool call, after both hook gates have allowed it. `flush` waits for the
queue to drain via `AsyncQueue.join()`, bounded by `FLUSH_TIMEOUT_SECONDS` (5s).

It runs for tool calls whose `mutates` is **not** `False`. The flag is three-valued and the
test is deliberately not `is True`: `None` means "depends on the arguments", which is what
a shell tool declares, and a shell command is the single most likely way an agent destroys
something. Reading `None` as "probably safe" would disable the checkpoint for exactly the
calls that most need it, silently.

Delegated agents and skills do not checkpoint at the delegation point — the child's own
tool calls do, and flushing twice would cost double and record nothing new.

On timeout, `flush` logs an error and returns `False`; the call proceeds. A lookup that
raises is caught and the call proceeds too.

## Alternatives considered

**Fail closed, as deepseek-harness does.** Their `session-checkpoint-policy` does not
invoke the tool body when the checkpoint fails, and re-checks the abort signal after the
flush because flushing takes time. It is the more rigorous position and it suits their
backend, which is a local file or SQLite write. Ours is a queue drained by a task in the
same event loop; the realistic failure is a wedged writer, and refusing to run any tool
until logging recovers turns a logging outage into a total outage. An agent that acts with
a gap in its log and says so loudly is the better product. This is a genuine trade, not an
oversight, and it is the part of this decision most likely to deserve revisiting.

**Flush on every tool call.** Simpler rule, no three-valued flag to reason about. Rejected
on cost: reads are the most frequent call in the system, and their loss costs a gap in a
transcript rather than an unanswerable question. Paying a queue drain on every `read` to
protect the occasional `write` puts the writer on the hot path of the common case.

**Flush on a timer instead — every N seconds.** Bounds the lag without touching the
dispatch path. It also bounds it to the wrong thing: the window that matters is the one
around a specific irreversible act, not a fixed interval, and a timer that happens to fire
just before the kill is luck rather than a guarantee.

**Also checkpoint before the model request, as deepseek-harness does.** They flush the
logged request prefix before the adapter is handed it. We cannot usefully copy that yet:
our trace records `agent_call` *after* the model returns, so at the moment of dispatch there
is nothing about the request in the queue to flush. Making that checkpoint meaningful would
first require logging the request before sending it, which is a larger change.

## Consequences

**What it bought.** After an interrupted run, the last tool call the agent was about to
make is on disk. "Did that command run?" becomes answerable — the record says the call was
dispatched, and the absence of a result says it did not finish.

**What it cost.** Every mutating call now waits for the queue to drain. In practice the
queue is near-empty and the wait is microseconds; under load it is real latency, paid by
exactly the calls that can change something.

**What it leaves open.** A flush that times out produces the original gap, with an error
line where there used to be silence. And nothing yet checkpoints around a model request —
see the alternative above.
