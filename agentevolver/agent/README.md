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
