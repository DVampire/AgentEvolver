---
name: hook
description: "Provides lifecycle interception points for tracing, compaction, registration, promotion, and other cross-cutting behavior."
version: 1.0.0
type: module
category: hook
requirements: []
metadata: {}
---
# Hook

Provides lifecycle interception points for tracing, compaction, registration, promotion,
and other cross-cutting behavior.

| File | Responsibility |
|---|---|
| `types.py` | Events, decisions, contexts, and hook contracts |
| `context.py` | Hook configuration and registration state |
| `server.py` | Ordered hook dispatch facade |
| `promotion.py` | Registration/promotion helpers |
| `default/` | Built-in hooks |

Built-in hooks (`default/`):

| Hook | Responsibility |
|---|---|
| `trace_hook` | Emits structured TraceEvents for every agent lifecycle event |
| `trajectory_hook` | Builds step-level training trajectories from lifecycle events |
| `memory_hook` | Feeds lifecycle events into the memory systems |
| `constraint_hook` | Enforces per-step resource budgets |
| `repeat_tool_reminder_hook` | Advises, never blocks, when a whole action batch repeats verbatim |
| `plan_mode_hook` | Refuses actions not declared free of effects until a person approves the plan |
| `compact` | Generic summariser for compressing record lists |
| `registration_hook` | Installs what an evolution run generated, for all eight component types |

Hooks observe or gate lifecycle events; core business logic stays in the owning module.
The no-progress hook is stateless: evidence and escalation counters are stored on each
Agent run, preventing concurrent sessions from affecting one another. It is wired into the
base `Agent._prepare_round`, so the guard applies to every agent uniformly rather than
being opted into per agent.
