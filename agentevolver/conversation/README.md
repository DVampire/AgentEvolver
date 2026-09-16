---
name: conversation
description: "Lines of dialogue inside a project — their transcripts and their identity. The middle of three levels: a project owns files and containers, a conversation owns memory and budgets, a task owns one submission."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Conversation

A **conversation** is one line of dialogue inside a project. It sits between
the project and the task:

| Level | Owns | Identified by |
|---|---|---|
| project | workspace files, kernels, containers | `session_id` |
| **conversation** | transcript, agent memory, budgets, todos | `conversation_id` |
| task | one submission's trajectory and traces | `task_id` |

```python
from agentevolver.conversation import conversation_manager

c = conversation_manager.create(owner, session_id, view="science")
conversation_manager.note_task(owner, session_id, c.id, task_id, "run a simulation")
conversation_manager.events(owner, session_id, c.id)
```

## Why the middle level exists

The two halves of a project scale differently.

Files and kernels are **resources**: one set, shared by every view and every
dialogue. Duplicating them per dialogue would mean a container per question.

Memory, token budgets and todos are **state**: they must not leak between
lines of work, or a fresh question arrives carrying the last one's context and
spending its tokens.

Before this split both hung off the same id, so there was no way to have the
first without the second. `ctx.id` is a conversation id now — it is the scope
of everything an agent accumulates — while anything that costs a container
stays keyed by project.

## The transcript is the file

`conversations/<id>.jsonl` is append-only and unbounded, and it is what
`conversation.events` reads. The Gateway's in-memory buffer only serves live
clients: it is capped and dies with the process, so a restored project would
otherwise reopen with an empty transcript over its own files.

`<id>.json` beside it holds the identity — title, view, timestamps, the tasks
submitted in it. The title comes from the opening message rather than a prompt:
sessions used to be named `web` or `interactive` by whoever created them, and a
sidebar of ten of them said nothing about any of them.

## Asking the person

A conversation is the only place in the system where a human is present, so a
question addressed to one lives here — `question.py`, alongside the transcript.

```python
from agentevolver.conversation import question_manager, UserQuestion

answers = await question_manager.ask(         # blocks until someone answers
    [UserQuestion(id="db", question="Which store?", options=[...])],
    session_id=ctx.id,
)
question_manager.answer(request_id, [{"id": "db", "selected": ["SQLite"]}])
```

The rendezvous is the runtime's `suspend`/`resume`, the same primitive escalation
uses. What differs is who answers. Escalation's answerer is the parent MetaAgent,
which already has the question in its inbox; a person has to be *shown* it, and
their answer arrives from outside the run. So two things are added and nothing
else:

- **Asking emits a trace event.** The Gateway republishes it as `trace.event`,
  tagged with the conversation that submitted the task, exactly as it republishes
  a tool call. A UI that already renders trace events sees the question with no
  second subscription.
- **The pending question is held and listable.** `question_manager.pending()`
  backs `question.list` at the Gateway, so a browser that reloaded between the
  question and the answer can still find it. A broadcast alone would strand the
  agent on an answer nobody can see they owe.

Held in memory and session-local: a question outlives neither the run that asked
it nor the person who was there to answer. The model-facing tool is
`ask_user_question`; `exit_plan_mode` asks through the same path with a
`plan-review` intent.

## Views

A conversation records which view opened it (`chat` / `science` / `canvas`).
The transcript is the same shape in each; the view decides what is rendered
beside it and lets a sidebar list one view's dialogues on their own.
