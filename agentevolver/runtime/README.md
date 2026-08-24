---
name: runtime
description: "Owns live Agent references, mailboxes, lifecycle state, and the event pump that advances registered Agent instances. Delegation lives here too: one agent running another is a ref plus a job, not a separate kind of agent."
version: 1.0.0
type: module
category: runtime
requirements: []
metadata: {}
---
# Runtime

Owns live Agent references, mailboxes, lifecycle state, and the event pump that advances
registered Agent instances.

| File | Responsibility |
|---|---|
| `types.py` | Agent references, statuses, and runtime messages |
| `pump.py` | Mailbox event pump |
| `server.py` | Spawn, send, stop, lookup, and delegation |

Runtime moves events and owns process-local execution state. Protocol defines conversation
semantics; Workflow interprets persisted orchestration programs above it.

## There is no sub-agent

Every actor is an `Agent`. "Sub" is a *relationship* — who dispatched whom — not a kind, so
it is carried as fields on the `AgentRef` of a running agent (`job_id`, `parent_session_id`,
`continuable`, `turns`) rather than by a second type in a second registry. This used to be
its own module, and the cost was exactly what a duplicated concept costs: two registries
answering "what is running", and a record whose live handles had to be kept in step with the
ref they shadowed.

## Blocking is the caller's choice, not the child's

`delegate()` waits for the child's whole run. `delegate_background()` returns a `Response`
carrying a job id as soon as the child holds its brief. Everything else — what the child
inherits, who it escalates to, where it reports — is identical, because the only thing
backgrounding changes is whether the caller spends its own steps waiting.

Both register the child as a `Job` of kind `agent`, so `job__list` / `job__output` /
`job__kill` reach a delegated child and a backgrounded shell command the same way. A
blocked parent gets its child's reports folded into the returned result, because it never
gets the chance to poll.

## One turn at a time

A background child has two queues in series, and the outer one is the point. Delivering two
tasks straight into `_inbox` starts a second run on the same ref while the first is still
going — `on_start` keys its run by ref name, so the second overwrites the first and the
first turn's result is lost with nothing logged. `_tasks` is drained by one driver
coroutine per child, which is also the handle `job__kill` cancels: the driver stops the
pump from its own `finally`, so a killed job cannot leave a child still calling a model.

`continuable` decides what happens after a turn. A one-shot child is released; a continuable
one goes idle, keeps its session and its memory, and can be handed more work with
`send_message_tool`. Idle is not finished, which is why "mid-turn" is a `busy` flag on the
ref rather than the job's status — collapsing the two would report either a live child as
collectable or a finished one as still running.
