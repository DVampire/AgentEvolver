---
name: job
description: Background work this session started — a backgrounded command, a backgrounded terminal send, a dispatched sub-agent, a reminder. What is still outstanding is shown every step.
version: 1.1.0
type: worker
---

<environment_job>

## What is in here

Four kinds of work, one registry, because they raise the same three questions — is it
done, what did it say, stop it:

| Started by | Appears as |
|---|---|
| `bash_tool(run_in_background=true)` | `bash: <command>` |
| `terminal__send(run_in_background=true)` | `terminal: <id>: <text>` |
| A dispatched background sub-agent | `agent: <name>` |
| `schedule_create_tool` | a reminder, `scheduled` until it is `DUE NOW` |

Giving each its own controller would be three vocabularies for one idea, and two of them
drifting behind the third. More concretely: an answer to "what am I still waiting on" that
covers only some of what is outstanding is worse than no answer, because the gap reads as
nothing.

## State

Everything **unfinished**, every step, without your asking.

That is the point of this being an environment. Background work is silent by construction:
a job that finished, one that failed and one that hung all look identical from outside —
like nothing at all. An agent that has to remember to check is being asked to remember the
thing it delegated the work in order to forget.

Finished jobs are not shown. They have said everything they are going to; their output
stays readable through `output`, and a line every step about something that is over is
prompt spent on nothing. `list` is where the whole history is.

## Reading a line

```
job_a13f  RUNNING                    252.4s  bash: pytest tests/
job_9c02  exited(0)                   38.1s  agent: reviewer_agent
job_5e71  scheduled/30m           in 12m 4s  reminder: check the deploy
```

**Elapsed time is the signal that separates working from hung.** The status is `RUNNING`
either way; a job that has printed nothing for minutes is telling you something the status
alone does not.

## Two things that are easy to conflate

**Killing a job is not stopping the work, when the job is watching a terminal.**
`kill` stops the *watching*; the command in the terminal keeps running.
`terminal__signal` is what stops that. Treating them as one lets "stop looking" read as
"stop running".

**Output that stops is not a job that finished.** `output` says `STILL RUNNING` explicitly
for exactly this reason — silence looks the same either way, and an agent that reads it as
finished stops collecting.

## Actions

| Action | What it does |
|---|---|
| `list` | Everything, finished included — the history the state deliberately trims |
| `output` | What a job has printed. Repeatable; reading does not consume. `tail` for the last N lines |
| `wait` | Hold one tool call until jobs finish or continuable agents become idle after a requested turn; returns early on failure/timeout |
| `kill` | Stop it. Output printed before the kill is kept |

</environment_job>
