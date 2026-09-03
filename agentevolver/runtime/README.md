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

`server.py`, `types.py` and `pump.py` are the previous mailbox runtime. They still drive
`agentevolver.agent.types.Agent` and everything registered against it, and are kept whole
so nothing that runs today stops running.

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

## Safe points

A safe point is any place the process voluntarily hands control back to the kernel. There
are two, and that is the entire definition:

- `gate()` — between steps of a turn. Signals apply; queued messages are delivered to the
  agent's `on_event`.
- `recv()` — the process is explicitly waiting: for a child's report, for a reply to an
  escalation, or, when idle, for its next turn.

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

### One pattern deliberately absent

PUSH/PULL — N workers competing for one queue — has no mode here. Dispatch names its
worker; nothing hands work to whoever is free. Peer-to-peer has none either: `send` takes
any pid, but nothing gives a process its sibling's, so two processes with no parent edge
have no way to reach each other and no authority relation if they did.

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
