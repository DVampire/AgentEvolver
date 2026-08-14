---
name: subagent
description: "Delegation the parent does not have to sit through: a child agent runs in the background as a job, and a continuable one stays alive to be given more work."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Subagent

Delegation the parent does not have to sit through: a child agent runs in the background
as a job, and a continuable one stays alive to be given more work.

| Path | Responsibility |
|---|---|
| `types.py` | `Subagent`, `ChildState` — what a live child is, and what separates a one-shot from a continuable one |
| `server.py` | `subagent_manager` — starts children, serializes their turns, carries what they say back, reaps them |

## Why it exists

Dispatching a sub-agent blocked the parent for the child's entire run. That is the defect
`agentevolver/job/` was built for, one level up: a step is meant to be a decision, and a
parent that spends fifty steps' worth of wall-clock inside one delegation made one
decision and waited. It also forces the work into a shape it may not have — an
orchestrator that wants three investigations running at once has to fan them out in a
single batch and then take all three results at once, or run them one after another.

## A background sub-agent is a Job

Not a parallel registry. A backgrounded shell command, a PTY send and a background child
raise the same three questions — is it done, what did it say, stop it — so the child is
registered as a `Job` of kind `agent` and the existing tools answer all three:

| Question | Call |
|---|---|
| what did I start | `job_list_tool()` |
| what has it said | `job_output_tool(job_id=…)` |
| stop it | `job_kill_tool(job_id=…)` |

The reference implementation ships `list_agents` and `interrupt_agent` for the same two
questions. Adding them here would mean two vocabularies for one idea, and the newer one
lagging: a listing that shows bash jobs but not children answers "what is running" wrongly
in exactly the situation the agent is confused.

Foreground delegations are registered too. It costs a record and it buys two things: "what
is running" stays true while a parent is blocked inside a delegation, and a child always
has somewhere to report, so `report_tool` does not behave differently depending on how its
parent happened to dispatch it.

## One-shot and continuable

`run_in_background` decides whether the parent waits. `continuable` decides whether the
child survives its answer.

- **One-shot** (the default). The child runs the brief, its result is appended to the job
  output, its ref is stopped. This is the fork: it answers once and ends.
- **Continuable**. The child stays alive between turns holding its own session — and
  therefore its memory and its context — and `send_message_tool` gives it another turn on
  the same conversation.

Turns on one child are serialized by the driver, one coroutine per child. Delivering two
tasks into an agent's inbox back to back starts two runs on the same ref, and the second
`on_start` overwrites the first run's record: the first turn's result is then lost with no
error anywhere. So a message sent to a working child waits for the current turn — it
cannot redirect work already underway, which is also what the model is told.

## What the child says, and how it gets back

Everything the child says accumulates in its job's output, in the order it said it:
mid-run reports, the turn's result, and the reason it failed. One transcript per child,
read with `job_output_tool`, because `job/README.md`'s contract is that a job is collected
rather than delivered — pushing a child's output into the parent's conversation would
insert content between a step's decision and its result.

`report_tool` is the child's write end. It is not the same channel as `escalate_tool`,
and the difference is worth keeping:

| | `report_tool` | `escalate_tool` |
|---|---|---|
| what it is | a line in the transcript | a rendezvous |
| does the child continue | yes, immediately | no, it is suspended until answered |
| who reads it | the parent, when it collects | the parent, now — it must reply or the child is stuck |
| the parent's move | `job_output_tool` | `reply_tool` |

Same for the parent's two write ends. `reply_tool` answers a child that is blocked inside
a step it was already permitted to take, so it declares itself read-only and plan mode
lets it through. `send_message_tool` hands a live child a fresh task, whose effects are
whatever the child then does — the same unknown that makes plan mode refuse an agent
dispatch outright — so it declares that it mutates and the same gate refuses it.

## Reaping

A background child is released with the run that started it, from
`Agent._release_session_resources`, alongside jobs, terminals and language servers.
Nothing else would ever stop one: the parent has concluded, and in a long-lived host the
child would go on calling a model on a task whose answer nobody can collect.

Killing is a stop, not a wrap-up. `job_kill_tool` cancels the child's driver, which stops
its pump where it stands; the child does not get to conclude, so its own trajectory ends
unfinished. Output written before the kill is kept, like any other job.

## What it is not

Not a scheduler and not a supervisor. Nothing here retries a child, restarts one, or
decides when to collect it. Children are session-local and depth-1: the registry a parent
reads holds the children *it* started, not their descendants, which is also the only depth
`send_message_tool` will act on.
