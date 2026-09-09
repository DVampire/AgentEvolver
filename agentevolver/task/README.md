---
name: task
description: "Models tasks and goals — their priorities, statuses and authority — loads task documents, and resolves CLI task input into normalized records."
version: 1.0.0
type: module
category: task
requirements: []
metadata: {}
---
# Task

Models tasks and goals — their priorities, statuses and authority — loads task documents,
and resolves CLI task input into normalized records.

| File | Responsibility |
|---|---|
| `types.py` | Task/Goal contracts, scheduling records, enums, and refusals |
| `context.py` | Task-document parsing and launcher-input normalization |
| `server.py` | `TaskManagerServer` scheduling, persistence, recovery, and workers |
| `goal.py` | `GoalStore`, owned by `task_manager.goals`, and goal authority rules |

Task records describe work; Agent and Workflow own execution behavior.

## Tasks and goals

A task is one submission: it is created, it runs, it ends, and the run ends with it. A
goal outlives every task in the session and says what the session is *for* — it is
written to `output/<owner>/sessions/<id>/goals.json` and is still there after a restart.

The two differ in who may change them. A task is the agent's to run. A goal is the
human's to set:

| Change | Who may make it |
|---|---|
| create, edit, pause, resume | direct human only |
| complete, blocked | the agent, or a human |
| read | anyone |

An agent that may rewrite its own objective does not have a goal, it has a note: whenever
the work got hard it could edit the target down to what it had already achieved, and its
own trajectory would read as success. Reporting *progress* is the opposite case — the
agent is the only party that knows whether the objective was met or whether it is stuck —
so `complete` and `blocked` are its to claim, and a human can read the claim and overturn
it.

## Declarative subscribers

An optional `runtime-input-manifest` declares `attachments`, `private_attachment_roles`,
and `subscribers`. Each subscriber has `id`, `agent`, a normal dispatch `brief`, and an
optional list of attachment IDs. `task.context` resolves those IDs against staged files
and expands only the assigned documents into the child brief. The parent receives public
subscriber/job bindings, never the expanded child briefs or private file paths.

The common `Agent.prepare_task` invokes runtime creation. A task can also declare a
`deployment` policy with `topic`, `required_releases` and `acceptance_subscriber`;
the deploy tool passes it to deployment manager on use. The base Agent preserves this
declaration without interpreting deployment rules. Other tasks omit this policy. Experiment-specific prompts
and counts belong to launchers, not to actor implementations.

This is model-input routing. Sandbox permissions remain a separate boundary.

Authority is never a tool argument. `authority_of(ctx)` derives it from two facts the
model cannot reach: the context is not a dispatched sub-agent, and the host stamped the
run with `human_turn` when it accepted a request a human actually made. A model that
could name its own authority would have it.

Every change names the revision it read (`revision` is a compare-and-set token). A caller
working from a stale view is told so rather than silently overwriting a change it never
saw — which for a goal usually *is* the news: someone else moved it.

## Optional execution evidence

`evolution.require_verified_improvement` enables task-scoped action receipts and an evolution
completion audit in `evolution.py`. The agent loop records actual actions before compaction;
registration, evaluated adoption and post-adoption consumer use must refer to the same version.
`run_policy.self_review` enables `self_review.py`: pinned preview and release browser visits
must include interaction and a later observation. Both explicit done and text-only completion
report unmet requirements as unsuccessful. Ordinary tasks retain their existing completion
behavior. These provenance checks do not provide an independent semantic quality judgment.
