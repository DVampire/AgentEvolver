# Retries happen in one place, and every attempt is recorded

Status: current

## Problem

This project's product is trajectories — the recorded runs are training data, not just
telemetry. That makes an unrecorded retry a different kind of defect than it would be
elsewhere: a call that failed twice and succeeded on the third try produced a trajectory
labelled `success`, indistinguishable from one that got it right immediately. The sample
says "the model did this correctly" about a model that had just failed twice.

Two mechanisms produced that, and they compounded.

**Retries were nested.** `ModelContextManager.__call__` looped `max_retries` times (3 by
default). Underneath it, every provider handed its own `max_retries` to the vendor SDK
— 5 for the OpenAI, Anthropic and Google adapters, 3 for one llm_hub route, 0 for two
others. The budgets multiply: three application attempts over five SDK attempts is fifteen
HTTP requests for one logical call. Nobody chose fifteen. The inner attempts reached
neither the log nor the trace, and the three defaults meant the real budget differed by
provider for no stated reason.

**There was no backoff.** The application loop retried immediately. Against the failure
retrying is supposed to help with — a rate limit, a half-open connection — three instant
attempts fail three times for the same reason, then look like three independent failures.

## Decision

One retry layer, in `ModelContextManager.__call__`. Every provider's `max_retries` is `0`,
carrying a comment that says why, and [test_model_retry.py](../../tests/test_model_retry.py)
discovers the defaults from the source so a provider added later is covered by the test
already existing.

That loop now waits between attempts — exponential from 1s, capped at 30s, with ±25%
jitter — and writes each failed attempt into the trace as a `CUSTOM` event with
`metadata.kind == "llm_retry"`, carrying the model, the attempt number and total, the
error, the wait about to happen, and the caller.

The delay is computed before the record is written, so the trace says how long the wait
will be rather than merely that there was one. The final attempt records a `null` delay,
which is how a reader tells "about to retry" from "gave up".

Recording never blocks or breaks the call: no session id means no record, and a trace
layer that raises is logged at debug and otherwise ignored.

## Alternatives considered

**Leave the SDK retries and delete ours.** Fewer moving parts, and the vendor
implementations already have good backoff. Rejected because the SDK layer cannot see what
this project needs recorded: it has no session, no caller, and no way to reach the trace.
Its retries would stay invisible, which is the defect being fixed.

**Keep both layers and just record ours.** The smallest change, and it leaves the
multiplication in place. A recorded budget of three sitting on top of an unrecorded budget
of five describes the run less accurately than no record at all, because it looks precise.

**Derive the attempt count from the log, as deepseek-harness does.** Their retry policy
recovers "which attempt is this" by scanning the session log for prior `llm/retry` events
keyed by turn, step, provider, and policy. That is the right design there, where a retry
spans steps: the loop closes the failed step, an error waterfall runs, and a later step
resumes. Our retries happen inside one call, so an in-memory counter is already correct and
the log is purely a record. Adopting the log-derived version would buy resumability we have
no way to use and add a scan to a hot path.

**Two events per retry — "scheduled" and "started" — also from deepseek-harness.** They
write one event before the cancellable wait and another after it, so a reader can tell a
retry that was waiting from one that began. Ours cannot be cancelled mid-wait, so the two
events would always appear together and the second would carry no information.

## Consequences

**What it bought.** A trajectory now contains its own failures. The number of requests one
logical call can make is a single number, chosen, and the same for every provider. Retries
back off, which is the only reason retrying helps against the errors that cause it.

**What it cost.** Retries are slower by design — a call that fails twice now takes at least
three seconds longer than it used to. That is the point, and it will look like a regression
in a latency graph.

**What it does not cover.** The trace records the failed attempts of one call; it does not
record the *fallback* to another model, which the same loop performs after retries are
exhausted. That path still only logs. It is worth doing and is not done here.
