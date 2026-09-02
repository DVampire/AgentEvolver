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
| `events.py` | `HookEvent` — every event name, and nothing else |
| `types.py` | Decisions, contexts, and hook contracts |
| `context.py` | Hook configuration and registration state |
| `server.py` | Ordered hook dispatch facade |
| `promotion.py` | Registration/promotion helpers |
| `default/` | Built-in hooks |

Built-in hooks (`default/`):

| Hook | Responsibility |
|---|---|
| `trace_hook` | Emits structured TraceEvents for every agent lifecycle event |
| `trajectory_hook` | Builds step-level training trajectories from lifecycle events |
| `project_memory_hook` | Learns verified cross-session project facts after task completion |
| `constraint_hook` | Enforces per-step resource budgets |
| `plan_mode_hook` | Refuses actions not declared free of effects until a person approves the plan |
| `compact` | Portable text-checkpoint fallback for compaction |
| `registration_hook` | Installs what an evolution run generated, for all eight component types |

## Where every hook sits

One run, top to bottom. Indentation is nesting: everything drawn inside a box happens
inside that scope, and a scope's own events bracket it. `▸` is a **gate** whose verdict
is binding; `·` is a **fact** that observers hear and cannot refuse.

```
kernel.spawn(agent, task)
│
├── · SESSION_START · USER_PROMPT_SUBMIT          root process
│   · SUBAGENT_START                              spawned child
│                                                 Kernel._announce("start")
├── ⟦ runtime phase ⟧  on_start(task, proc)       the agent's own method, not an event
│
└── agent.__call__ → _run                         ┌ AGENT RUN ─────────────────┐
    ├── · ON_START                                │                            │
    │                                             │                            │
    ├── for step in range(max_step):              │ ┌ STEP ──────────────────┐ │
    │   ├── proc.gate()                           │ │ THE ONLY SAFE POINT    │ │
    │   │   ├─ suspend → ⟦on_suspend⟧ · ON_SUSPEND│ │ signals and messages   │ │
    │   │   ├─ resume  → ⟦on_resume⟧  · ON_RESUME │ │ land here and nowhere  │ │
    │   │   ├─ stop    → Stopped ⇢ landing        │ │ else, so neither can   │ │
    │   │   └─ message → ⟦on_event⟧   (no event)  │ │ arrive mid-turn        │ │
    │   │                                         │ └────────────────────────┘ │
    │   ├── _live_blocks → middleware             │                            │
    │   │   ├─ LandingWindow      → text          │                            │
    │   │   ├─ NoProgress         → text          │                            │
    │   │   ├─ RepeatedActions    → text          │                            │
    │   │   └─ Constraints  ▸ constraint_hook     │ may end the run            │
    │   ├── · PRE_STEP                            │ announced only once the    │
    │   │                                         │ step will really happen    │
    │   ├── _fold_if_needed → make_room           │                            │
    │   │   └─ · PRE_COMPACT … · POST_COMPACT     │                            │
    │   │                                         │                            │
    │   ├── think()                               │ ONE MODEL CALL             │
    │   │                                         │                            │
    │   ├── act() → ActionExecutor                │ ┌ ACTION (per call) ─────┐ │
    │   │   ├── router.denial()                   │ │ static allowlist       │ │
    │   │   ├── ▸ plan_mode_hook  (PRE_ACTION)    │ │ may deny               │ │
    │   │   ├── · PRE_ACTION                      │ │ carries the denial     │ │
    │   │   ├── router.invoke() by capability:    │ │                        │ │
    │   │   │   │                                 │ │                        │ │
    │   │   │   ├─ tool ┐                         │ │ ┌ INVOCATION ────────┐ │ │
    │   │   │   ├─ environment ├→ shared pipeline │ │ │ ▸ PRE_INVOKE       │ │ │
    │   │   │   ├─ connector ┘                    │ │ │ RESOLVE → VALIDATE │ │ │
    │   │   │   │                                 │ │ │ GUARD              │ │ │
    │   │   │   │                                 │ │ │  ▸ PERMISSION_…    │ │ │
    │   │   │   │                                 │ │ │ PREPARE → EXECUTE  │ │ │
    │   │   │   │                                 │ │ │ POST_EXECUTE       │ │ │
    │   │   │   │                                 │ │ │ · POST_INVOKE      │ │ │
    │   │   │   │                                 │ │ │ · INVOKE_FAILED    │ │ │
    │   │   │   │                                 │ │ └────────────────────┘ │ │
    │   │   │   ├─ agent → kernel.spawn(child)    │ │ this whole table again │ │
    │   │   │   │          · ON_ESCALATE if it    │ │ one level down         │ │
    │   │   │   │            blocks on its parent │ │                        │ │
    │   │   │   └─ skill · workflow · plugin      │ │ manager(), NO pipeline │ │
    │   │   └── · POST_ACTION                     │ └────────────────────────┘ │
    │   └── · POST_STEP                           │                            │
    └── Kernel._exit                              └────────────────────────────┘
        ├── ⟦on_land(reason)⟧      every graceful ending, a normal finish included
        ├── ⟦on_exit(status)⟧      after the process is marked exited
        └── · TASK_COMPLETED · SESSION_END  │  · SUBAGENT_STOP
```

Outside every box: `WORKTREE_CREATE` / `WORKTREE_REMOVE`, raised by `IsolatedWorktree`
when a child gets its own checkout, and `DIRECT_CALL`, which is not a lifecycle event at
all — `hook_manager` requires an `event` key even when a hook is called by name as a
service, and that is the honest marker for it.

## The three "pre" events are three different levels

They are the ones most easily confused, so state them side by side:

| | fires when | times per run | can refuse | consumers |
|---|---|---|---|---|
| `PRE_STEP` | a step is about to run, before the model is called | ≤ `max_step` | yes, via `constraint_hook` | `trace`, `constraint` |
| `PRE_ACTION` | the model has decided what to call, before routing | once per proposed call | yes, via `plan_mode_hook` | `trace`, `plan_mode` |
| `PRE_INVOKE` | the call is routed and about to really run | once per call that funnels through the pipeline | yes | none yet |

Two distinctions in that table are easy to lose and both have been lost before:

- **`on_stop` is not the stop signal.** `ON_STOP` means the agent decided it was done. A
  stop signal lands in the runtime's `on_land` phase, which fires on *every* graceful
  ending including a normal finish. One name for two opposite causes is how an observer
  records a cancellation as a completion.
- **An action is not an invocation.** `PRE_ACTION` sees a call the model wants to make,
  whichever of the seven capability kinds it is; `PRE_INVOKE` sees one that is about to
  run, one level down, with its arguments canonicalised. A call denied at the action gate
  never reaches an implementation at all. The invocation level covers only `tool`,
  `environment` and `connector` — the three that share `ToolExecutionPipeline` — and
  `capability_type` in the payload says which. `skill`, `workflow`, `plugin` and `agent`
  have no invocation events, so a hook that watches only that level cannot see them.

## Three ways a hook is reached

Same registry, three delivery shapes, and mixing them up is how a hook goes quiet.

| Shape | Who hears it | Binding | Raised by |
|---|---|---|---|
| `events.emit(event, …)` | the two names in `OBSERVERS` — `trace_hook`, `trajectory_hook` | no | the loop and the executor: step and action events |
| `events.gate(name, …)` | exactly one hook, by name | **yes** | `Constraints`, the executor's plan-mode check |
| `events.broadcast(event, …)` / `hook_manager.emit` | every hook whose `events` list names it | no | the kernel, the pipeline, the sandbox |

The consequence is easy to miss and was missed: **subscription only works for the events
raised by `broadcast`.** Step- and action-level events go out through `emit`, which calls
two hardcoded names, so a hook that declares `events = [HookEvent.POST_STEP]` and
registers cleanly is never called. `repeat_tool_reminder_hook` sat in exactly that gap —
it subscribed to nothing, was named by nobody, and was not an observer, yet the module
listed it as active. It is now `RepeatedActions` in `agent/loop/guards.py`, because
advice to the model has one channel: middleware.

Every event has exactly one firing site, reached through the enum rather than a bare
string. An event with no site is worse than a missing one: a handler registers for it,
never runs, and reads as a policy that is in force. `tests/test_hook_coverage.py` holds
both halves of that.

Hooks observe or gate; business logic stays in the owning module. Per-session memory
receives the exact numbered event from `trace_hook` rather than translating lifecycle to
Trace a second time, which preserves compaction source identities.

Neither guard that advises the model is a hook: `NoProgress` and `RepeatedActions` are
middleware in `agent/loop/guards.py`, mounted by the orchestrator that wants them. They
see different shapes and neither sees the other's — `NoProgress` catches many *different*
read-only measurements, which no repeat detector can see because every call differs;
`RepeatedActions` catches the identical batch issued again, which `NoProgress` cannot see
because a repeated write is not read-only. Both are stateless, recomputed from the
conversation each step, so concurrent sessions cannot trip one another.
