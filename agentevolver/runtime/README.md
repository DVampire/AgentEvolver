---
name: runtime
description: "The process kernel: agents are processes, the kernel owns their states, their two channels and their lifecycle. Dispatch and subscription are the same mechanism with one flag between them. The legacy mailbox manager still drives the old Agent base class until the last actor moves across."
version: 1.0.0
type: module
category: runtime
requirements: []
metadata: {}
---
# Runtime

**Runtime is the kernel; an agent is a process.** The kernel owns three things and no
more: when a process may run, when a message reaches it, and how it is created and
reaped. What happens inside a turn belongs to the agent, and the kernel never looks.

That is why a model-driven agent, a deterministic procedure and an orchestrator are the
same kind of thing here — each is an object with a `__call__` and some optional hooks.

| File | Responsibility |
|---|---|
| `states.py` | The six states and the transitions the kernel will make |
| `signals.py` | The preemptive channel: one coalescing slot per process |
| `envelopes.py` | What travels on a mailbox: task, event, report, reply |
| `mailbox.py` | One process's FIFO inbox |
| `process.py` | The process control block, and the safe points |
| `modes.py` | The three endpoint roles, and what each means to the kernel |
| `topics.py` | Topic ↔ pid subscriptions |
| `kernel.py` | Process table, turn driver, IPC, lifecycle |
| `errors.py` | Kernel errors, and the two control-flow signals |

`Kernel` drives the registered Agent implementations through their `__call__` contract.

## Six states, one exit

```
NEW ──▶ RUNNING ⇄ IDLE          IDLE is "alive with no work" — a resident
         │  ⇅       ⇅            process between turns. It is neither running
         │  SUSPENDED            nor finished, and collapsing it into either
         ▼                       reports a live child as collectable or a
      STOPPING ──▶ EXITED        finished one as still running.
```

This replaces `status` + `busy` + `paused` + `continuable` + `_resume_gate`, five flags
that answered the same question and could disagree. Every ending — finished, failed,
stopped, killed — goes through `STOPPING`, so clean-up, the parent notification and the
exit status exist once instead of once per outcome.

A one-shot Agent's unsuccessful result yields a `FAILED` exit and a failed parent
report, even when it returned normally rather than raising. A resident process records
that assignment's failure in `turn_success` and remains available for later assignments.

## Safe points

`wait()` joins the cleanup task as well as the turn driver. Parents stop and join their
children before reporting their own exit. Repeated forced stops do not cancel cleanup;
stopping a process before its driver first runs still executes its exit path.
`shutdown(timeout=...)` returns any still-pending PIDs and retains their records; it
does not pretend the deadline killed external resources. Another shutdown can collect
them once cleanup finishes. New spawning is refused while shutdown remains pending.
Cleanup exceptions are preserved in `cleanup_errors` in snapshots and exit events;
task success does not assert that every external resource was successfully released.

Agent exit retries each resource release a bounded number of times. Failed Job/Terminal
stops retain their registry entries for a later cleanup attempt; accepting cancellation
of an asyncio task does not mark it killed before its cancellation cleanup completes.
An OS job must own a dedicated process group before that group can be signalled. This
does not establish control over descendants that deliberately escape the group; that
requires an owning OS/container supervisor, not just an in-memory process table.

### Isolated child workspaces

One-shot dispatch can opt into `isolate_worktree`. The runtime owns its lifetime and
returns `artifacts.patch` on exit, including on cancellation. The patch is saved atomically
before removing the worktree. A failed save, removal, or preceding resource cleanup keeps
the worktree path in `artifacts.worktree` and records `cleanup_errors`; no auto-merge is
performed. Paths use `P.LOG_WORKTREE` / `P.LOG_WORKTREE_PATCH` under the run log directory.

Workspace resolution is task-local and inherited by child tasks. Permission ceilings
are retained while the exact parent workspace fence is relocated to the private tree.
The shared session binding is leased until runtime cleanup completes: rebinding or changing
global path overrides during a run is rejected. This is **not** arbitrary multi-root
concurrency in one interpreter: managers still hold session-scoped state. Use separate
OS processes for different root sessions, or stop and join before rebinding.

Every root and its descendants share a `RunBudget`, including resident turns and
children later removed from the process table. Reported model/compaction usage
counts cached input once, plus output; costs stay reported, estimated, or unknown.
With a bound session, `P.SESSION_RUN_STATE` stores an atomic per-thread ledger.
Explicit resume restores consumption and validates the whole document before use.
An existing ledger requires resume or a new thread ID; missing/corrupt legacy ledgers
require an explicit migration, never silently reset to zero.

The runtime scopes the ledger through model and lifecycle calls. Model Manager reserves
estimated input plus the output allowance before each framework-visible buffered,
streamed, fallback, or native-compaction attempt. Synchronous reservation prevents
concurrent children spending the same remaining budget. Receipt IDs prevent the Agent's
per-turn accounting from charging a settled request twice. Reservations are estimates,
not a provider-enforced billing cap; provider-internal activity is only as observable as
its returned usage.

Version 2 ledgers persist request receipts. Missing usage, cancellation, or a crash leaves
an `unknown` reservation, not zero consumption; further model calls are refused until
the host reconciles it with `budget.reconcile(receipt, usage, evidence=...)`. Reconciliation
is idempotent and rejects conflicting totals. A definite typed pre-execution feature
rejection can release the reservation for a portable fallback. No automatic billing API
is assumed: provider-side audit evidence is required for unknown requests. Snapshots
expose reserved tokens and unreconciled receipt IDs. Version 1 usage-only ledgers remain
readable but cannot retroactively reveal their missing requests.
It does not restore mailboxes, subscriptions, browser state, or external jobs.

A safe point is any place the process voluntarily hands control back to the kernel. There
are two, and that is the entire definition:

- `gate()` — between steps of a turn. Signals apply; queued messages are delivered to the
  agent's `on_event`.
- `recv()` — the process is explicitly waiting: for a child's report, for a reply to an
  escalation, or, when idle, for its next turn.

`send()` still returns acceptance into the local mailbox, not model acknowledgement.
`Process.snapshot()["deliveries"]` exposes receipt states keyed by envelope ID:
`queued`, `received`, `delivered`, `failed`, `unhandled`, `interrupted`, `undelivered`.
`delivered` means the local handler/turn returned, not that a remote model obeyed it.
Queued receipts and the most recent 128 terminal receipts are retained. Retransmitting
an ID still in that window does not invoke the handler twice; this is not durable
exactly-once delivery. Queued messages left on shutdown become `undelivered`.

`ask_parent()` accepts only a reply naming its active question and parent sender.
`kernel.reply()` binds an omitted question ID to the current pending question, and
rejects stale IDs or a child that is no longer waiting. Unrelated incoming messages
remain observable and do not restart the question's timeout.

Suspending or stopping anywhere else cuts a turn in half — the model has emitted tool
calls whose results are not all recorded — and a conversation in that shape is rejected
by strict provider validation on the next request.

| Operation | Takes effect | Conversation | Analogue |
|---|---|---|---|
| `suspend` | step boundary | whole; resumes losslessly | SIGSTOP |
| `stop` | step boundary, plus a landing hook | whole; partial result usable | SIGTERM |
| `stop(force=True)` | action boundary | not guaranteed; exits CANCELLED | SIGKILL |
| message delivery | `gate()`, or inside `recv()` | whole | signal at a syscall return |

## Phases are methods, events are notifications

Both are called "hooks" in conversation and they are not the same thing. A **phase** is a
method on the agent that the kernel calls, asking *this process* to do something about a
transition. An **event** is a notification to everything outside the process that the
transition happened. Every phase below is paired with the event raised alongside it, and
three phases deliberately have none.

| Phase | Called from | Means | Event raised with it |
|---|---|---|---|
| `on_start(task, proc)` | `_serve`, per input | a turn is about to begin | `SESSION_START` + `USER_PROMPT_SUBMIT` for a root, `SUBAGENT_START` for a child |
| `on_event(envelope, proc)` | `gate()` / `recv()` | a message was delivered | none — it becomes context, and whoever sent it already announced it |
| `on_suspend()` | `_hold()` | held; release volatile resources | `ON_SUSPEND` |
| `on_resume()` | `_hold()` | released; rebuild what you let go | `ON_RESUME` |
| `on_land(reason)` | `_exit`, when graceful | the one chance to persist a partial result | none — the outcome is announced at exit |
| `on_exit(status)` | `_exit`, always | release what the run left running | `TASK_COMPLETED` + `SESSION_END` \| `SUBAGENT_STOP` |

`on_land` is **not** the stop signal's handler. It runs on every graceful ending, a normal
finish included, which is why it is named for the landing rather than for the signal.
`HookEvent.ON_STOP` already means the other thing — the agent decided it was done — and
one name for two opposite causes is how an observer records a cancellation as a
completion.

Two properties hold the whole arrangement together:

- **A phase never interrupts a step.** The only safe points are `gate()` and `recv()`, so
  a suspend, a stop or a delivered message is always between turns. Step- and
  action-level events therefore fire strictly inside `RUNNING`, nested wholly within one
  `on_start` … `on_exit` bracket.
- **A phase can refuse nothing.** `Process._hook` swallows an agent's exception and logs
  it; only `Stopped` and `Killed` propagate. An agent whose `on_land` raises still exits,
  because a process whose clean-up failed is still a process that ended.

`agentevolver/hook/README.md` draws every event's position relative to these phases.

## Capability sync: one store, three ways in

A component evolved mid-run has to reach the participants that need it. Two different
things travel, and only one of them is a problem.

**Visibility is already solved, in every topology.** Registering a component moves
`extension_manager.capability_revision`, every roster is keyed on it, and each process
rebuilds on its next step. That is a broadcast: it does not ask who exists, so dispatch
and subscription are served identically and nothing needs arranging.

**Permission is the design.** A roster bound by `capability_allowlists` states an
isolation contract — a website visitor holds one tool and must keep meeting the product
through the page — so a component that did not exist when the contract was written cannot
enter it by itself. Permission lives in one place, the process's own context, and there
are three ways to write it. Which one applies is decided by two questions:

```
                         does the process exist yet?
                                    │
              ┌─────────────────────┴─────────────────────┐
             no                                          yes
              │                                           │
     ① with its creation                        can the granter address it?
       dispatch args carry                                │
       tool_allowlist, …                    ┌─────────────┴─────────────┐
                                           yes                          no
                                            │                            │
                                  ② by pid, afterwards        ③ declared in advance
                                    adoption_tool grant        accepts_evolved
```

| Mode | Exists when evolved? | Addressable? | Path |
|---|---|---|---|
| `RESPONDER` | no — the dispatch comes after | — | ① |
| `SERVICE` | yes | yes, a pid is how it is addressed at all | ② |
| `SUBSCRIBER` spawned by the granter | yes | yes, via `children()` | ② |
| `SUBSCRIBER` spawned by someone else | yes | **no** | ③ |

`RESPONDER` has no sync problem: the grant rides with the dispatch, so the ordering is
right by construction. The last row is the only hole, and only ③ can close it — a grant
needs an edge between granter and granted, and a publisher deliberately has none to its
subscribers. Declaring in advance needs no edge.

### What acceptance actually promises

Acceptance is **provisional**. A component is live the moment it registers — there is no
candidate pool and no promotion step — so a declared type admits something that has not
been evaluated and may still be rolled back. That is the register-is-live contract rather
than a gap, but it means `accepts_evolved` says "I will work with what this run
produces", not "with what this run has proven".

A granted name whose component later unloads stays in the list and is skipped: a roster
is built from what is registered, so a dead name reaches no model. The alternative — a
scope that raises once a component unloads — would take down every step of every process
that had been granted it.

### Who declares what

`accepts_evolved` is a field, so it can be set in two places that mean different things.
An actor states the conservative default, because the isolation contract is a property of
the role and should be legible in the file that defines it. A config may widen it for one
deployment without editing the framework.

`Process.snapshot()` reports grants separately from defaults, so a listing can answer
which processes hold what a run evolved. Only grants: reporting a default as a grant
would say every restricted agent had been granted its own restriction.

## Two channels, and what is on neither

Signals are preemptive and coalescing: a process told to stop three times stops once, and
a stop never queues behind a hundred events. Messages are FIFO and delivered only at safe
points.

Action results are on neither. A tool's output is the process's own local data, not a
message between processes — routing it through the mailbox is what made the previous
turn loop span four entry points and need hand-rolled reordering of external notes
around an in-flight batch.

## Interaction modes: three endpoint roles

How work reaches a process is declared, not assembled. `modes.py` holds the three roles
and what each means to the kernel; `spawn(mode=...)` is the only place that decides.

| Mode | Runs on spawn | Addressed by | Kernel derives | Networking analogue |
|---|---|---|---|---|
| `RESPONDER` | yes | — (answers once, exits) | `resident=False` | REP |
| `SERVICE` | yes | **pid** | `resident=True`, `start_idle=False` | ROUTER / DEALER |
| `SUBSCRIBER` | **no** | **topic** | `resident=True`, `start_idle=True`, topics required | SUB |

```
                      ┌──────────── one task in, one answer out ────────────┐
   orchestrator ──────▶  RESPONDER                                          │
      │  spawn(mode=RESPONDER)            ReportEnvelope ───────────────────┘
      │
      │            ┌──── resident; whoever holds the pid keeps talking ─────┐
      ├───────────▶  SERVICE  ◀── send_task(pid) ──── orchestrator          │
      │  spawn(mode=SERVICE)              parks IDLE between turns ─────────┘
      │
      │            ┌──── resident; the publisher never learns who listens ──┐
      └───────────▶  SUBSCRIBER  ◀─┐                                        │
         spawn(mode=SUBSCRIBER,     │                                        │
               topics=[...])        │      ┌─────────────────────────┐       │
                                    └──────┤  topic index (kernel)   │◀──────┼── publish(topic)
                     SUBSCRIBER  ◀─────────┤  {root}::{name} → pids  │       │   from ANY process
                     SUBSCRIBER  ◀─────────┘─────────────────────────┘       │   in the task tree
                                           standing brief leads every turn ──┘
```

**A mode is the inbound half only.** Whether a process may start others belongs to the
agent template, not to its lifecycle, and lives there as `include_agents` — the
difference between a leaf and an orchestrator. The two are independent, and one process
routinely holds several roles at once, exactly as one ZeroMQ process holds several
sockets: the website builder answers a task, dispatches sub-agents, and publishes
releases.

| Participant | Inbound | Outbound | Also |
|---|---|---|---|
| `meta_agent` | `RESPONDER` | orchestrator | — |
| `website_builder_agent` | `RESPONDER` | orchestrator | publishes releases |
| `website_user_agent` | `SUBSCRIBER` | leaf | — |
| `browser_agent` (acceptance) | `SUBSCRIBER` | leaf | — |
| `code_agent`, `monitor_agent` | `RESPONDER` | leaf | — |

The same template takes different modes on different spawns — `website_user_agent` is a
subscriber in the co-design panel and a `RESPONDER` when run standalone — which is why
the mode is a property of the process and not of the class.

### Why the names are roles and not topologies

Networking separates three questions, and conflating them is what made this hard to name:

- **Topology** — star, bus, tree, mesh. Describes a whole system, so it belongs in a
  diagram like the one above rather than in a field on a participant.
- **Message exchange pattern** — request/reply, publish/subscribe, one-way. Describes an
  interaction, which has two ends.
- **Endpoint role** — what one participant does. This is the only one of the three a
  single process can declare about itself.

ZeroMQ's decision is the one worth borrowing: there is no `PUBSUB` socket, only `PUB` and
`SUB`. A participant labelled "broadcast" is either the publisher or one of the
listeners, and nothing can tell which — so the label names the endpoint.

### Why a name at all

"Subscriber" was not a thing this runtime knew. It was `resident=True` plus
`topics=[...]` plus `start_idle=True` plus a context of its own plus a standing brief
that leads every turn — five facts, assembled by hand at each call site, where getting
one wrong raised nothing and produced a process that looked spawned and did nothing.
Three defects came from exactly that, each costing a whole run:

- subscribers spawned with the parent's context shared one browser tab, so each read a
  page another had just navigated away from and reported having no browser at all;
- a standing brief was dropped on the direct-message path, so a participant answered
  "NO ASSIGNED CONTEXT" about a persona it had been handed;
- a topic registered unscoped while the publisher looked up a scoped name, so every
  fan-out reached nobody and reported success.

A mode refuses the contradictions instead: a `SUBSCRIBER` with no topic waits for an
event nobody can address to it, and a `RESPONDER` with one registers an edge it drops
moments later. Both are rejected at `spawn`, where the mistake is, rather than found in a
run that produced no output.

### Every shape, and the two decisions

`publish` fans out; `assign` gives the same work to exactly ONE subscriber, preferring
whoever is idle and then whoever waited longest. That is the competing-consumers
discipline — PUSH/PULL rather than PUB/SUB — and it is what makes a pool of
interchangeable workers expressible: fanning out would have every worker do the whole
job, and naming the worker is dispatch rather than a pool.

`tests/test_topologies.py` constructs each of these from the primitives, so the claim is
checkable rather than asserted:

| Shape | Built from |
|---|---|
| Star / hub-and-spoke | orchestrator + N `RESPONDER` |
| Tree | orchestrators nested; depth is not special-cased |
| Pipeline | a chain of dispatches — a degenerate tree |
| Scatter/gather | fan out, collect the reports the kernel posts on exit |
| Bus (1→N) | publisher + N `SUBSCRIBER` |
| Bus (N→N) | any process in the task tree may publish |
| Mesh / peer-to-peer | each peer subscribes to a topic named after itself |
| Worker pool | N `SUBSCRIBER` on one topic + `assign` |
| Request/reply, upward | `ask_parent` / `reply` along the parent edge |
| Request/reply, async | a `SERVICE` and whoever holds its pid |

Two things are deliberate rather than missing.

**The dispatch graph stays acyclic** — one parent each, by construction. A cycle in
supervision has no meaning; cycles between participants live on the bus, where a topic
edge carries no lifecycle and A answering B answering A supervises nothing.

**Peer addressing by pid is reachable but never handed out.** `list(session_id=...)`
enumerates a session and `send` does not check kinship, so the kernel does not forbid it.
No capability hands one process another's pid, which keeps it a deliberate act. Nothing
is lost by that: a peer that subscribes to its own topic is addressable by any sibling
without either holding a pid or having authority over the other.

## Dispatch and subscription are one mechanism

```python
child = await kernel.spawn(agent, task, mode=RESPONDER, parent=self.proc)  # fork
result = await kernel.wait(child)                                          # waitpid

watcher = await kernel.spawn(agent, brief, mode=SUBSCRIBER,                # daemon
                             topics=["deploy"], parent=self.proc)
await kernel.publish_scoped("deploy", "finished", {...}, ctx=ctx)          # one turn each
```

A dispatching parent does not have to block. When a child exits, the kernel posts a final
`ReportEnvelope` to its parent — the equivalent of SIGCHLD — so a parent can park in
`recv()` and collect children as they finish instead of polling. Escalation needs no
mechanism of its own either: `Process.ask_parent` sends a report marked `blocked` and
then waits for a message, which is what a blocked child is.

One process table and one mailbox serve both. What differs is the mode's lifecycle and
which index finds the process — the pid table or the topic index — and a subscriber
registers IDLE rather than spending a turn on work that has not arrived.
