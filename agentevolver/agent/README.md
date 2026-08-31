---
name: agent
description: "Defines executable agents and their lifecycle. `Agent` provides the event-driven run loop; `ProceduralAgent` supports deterministic procedures; `AgentManagerServer` exposes agents to callers and multi-agent orchestrators."
version: 1.0.0
type: module
category: agent
requirements: []
metadata: {}
---
# Agent

Defines executable agents and their lifecycle. `Agent` provides the event-driven run loop;
`ProceduralAgent` supports deterministic procedures; `AgentManagerServer` exposes agents
to callers and multi-agent orchestrators.

| Path | Responsibility |
|---|---|
| `types.py` | Agent contracts, contexts, execution loop, and dispatch behavior |
| `context.py` | Registration, construction, versions, and instance lifecycle |
| `server.py` | Stable manager API, execution, and capability schemas |
| `native_tools.py` | Compose callable capabilities and their dispatch routes |
| `actor/`, `generator/`, `evaluator/`, `optimizer/` | Built-in agent roles |

Agent owns single-agent behavior. Cross-agent scheduling belongs to Runtime, Protocol, and
Workflow rather than to an Agent subtype.

## Subscription-triggered turns

`Agent.subscription_topics` declares default logical topics for instances that are started
as continuable background subscribers. A publish does not bypass the Agent lifecycle:
`SubscriptionEventMessage` reaches `Agent.on_subscription_event()`, whose default behavior
uses the same task handler and `on_start → act → on_end` path as a directly assigned turn.
Subclasses override the event hook only for boundary setup, then call `super()`.

Subscription state is not stored on the Agent template. The live `AgentRef` owns the scoped
topic edges, standing brief, and attachments; Runtime queues events one at a time. This keeps
parallel invocations of one registered Agent isolated and prevents a published task from
overwriting an active run.

## MetaAgent uses the same loop

MetaAgent is not a benchmark-only implementation. It runs the same think/dispatch loop
and receives a broader native action roster assembled from the six evolvable capability
families: `tool`, `skill`, `connector`, `agent`, `workflow`, and `plugin`. Baseline configs
disable promotion with `enable_evolving=False`; evolution configs change policy, not the
conversation or dispatch architecture. This keeps checkpointing, provider-state replay,
tool execution, and capability lineage identical in both modes.

Domain orchestrators may subclass `MetaAgent` without becoming configuration aliases.
`WebsiteBuilderAgent`, for example, is independently registered as
`website_builder_agent`: it owns a dedicated prompt, configuration, runtime identity,
version/trace history, and evolution policy while reusing the standard orchestration loop.
The website demo launcher selects that registered Agent explicitly; it does not relabel or
invoke `meta_agent`.

## Bounded delegation contract

All orchestrators expose the same structured child contract: a concise `task`, attached
`files`, `read_set`, `write_set`, and independently checkable `acceptance` conditions. The
dispatcher copies the resource and acceptance fields into the child's fixed inherited context;
they are not metadata visible only after the child finishes.

`task` is deliberately limited to 12,000 characters and the structured lists are bounded in
both the native JSON schema and the runtime dispatcher. Longer designs, requirements, or source
material must be written as workspace artifacts and passed through `files`. This prevents a model
from turning a delegation call into an unbounded generated document, makes the exact specification
auditable, and lets multiple specialist agents consume the same source without paraphrase drift.

## Agent versus Tool execution policy

Agent still owns decisions that apply to every capability kind: assembling the visible
roster, plan-mode lifecycle, the per-turn action batch, and the durability checkpoint
before a possible side effect. Once a route resolves to a Tool, Agent does not execute its
body or reinterpret its result. It passes an execution context to Tool Manager containing
the model call ID, Code Mode parent/root IDs, session/task/agent/step coordinates, and any
plan/read-only denial.

That denial is data, not an exception. Tool Manager settles it as `policy_denied` without
entering the body, after which the normal POST_ACTION hooks still run. Consequently Trace
has a paired start/result for a refused call rather than a dangling `tool_start`, and the
model receives the same actionable refusal whether it called natively or from Code Mode.

Code Mode sub-calls call `Agent._run_one()` again. They therefore acquire the same plan
gate and Agent restrictions, then enter the exact same Tool pipeline; `parent_call_id`
only adds lineage and never selects a privileged dispatcher.

### Trace integrity boundary

Agent resolves `trace_integrity_profile` from the Session context and propagates it through
ToolContext. Tool Manager owns the possibly-mutating boundary because only its pipeline
knows when Tool-owned permission, extension guards, and one-shot approval have all
settled. It checkpoints at that exact point, directly before the body. `mutates=False` is
the only read-only declaration; `None` remains conservative. Code Mode sub-calls re-enter
the same Tool pipeline and cannot bypass either approval or durability.

After memory, Trace, snapshot, and trajectory POST_STEP hooks complete, Agent checkpoints
the step as one semantic unit. It also forwards the profile to Model Manager, whose
request-snapshot checkpoint runs before each actual primary/fallback route. Training and
high-risk failures propagate rather than being converted into ordinary model retries.
