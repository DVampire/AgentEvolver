---
name: terminal
description: Terminals that stay open between calls — a shell that keeps its directory, its virtualenv, its ssh session and its REPL.
version: 1.0.0
type: worker
---

<environment_terminal>

## What this is for

Every `bash_tool` call is a fresh process. Everything a command changed **about its own
environment** is gone by the next one:

```bash
cd /deep/path             # next call is back in the workspace
source venv/bin/activate  # next call is not in the venv
ssh host                  # next call is local again
python                    # next call has no REPL
gdb ./program             # breakpoints and frames gone
```

A terminal here holds a real pty open, so the shell stays exactly where you left it.

Use it when **the state between commands is the point**. For a single command that stands
on its own, `bash_tool` costs one call rather than an open, a send and a close.

## State

The current screen of every **live** terminal arrives in `environment-state` each step,
labelled with its id, without your asking. That is what makes this an environment rather
than a set of tools: you are looking at your terminals, not fetching them.

Closed terminals are not shown — they have nothing live to show, and their output is still
reachable through `read`.

## Silence is the only completion signal

A terminal has nothing that says "the command finished". The shell is there either way. So
`send` returns a **wait reason**, and reading it is the difference between knowing and
assuming:

| Reason | What happened | What to do |
|---|---|---|
| `idle` | It stopped printing | Usually done. **A command paused mid-way looks identical** — if the output stops somewhere implausible, send `text: ""` with `submit: false` and look again. |
| `timeout` | Still printing when the budget ran out | Nothing is lost. The command runs on and output keeps accumulating; read it, or send again. |
| `exited` | The shell itself is gone | Its output is still readable. Open a new terminal. |

## Interrupting is not closing

`signal` goes to the **command currently running**, not to the shell — so the terminal
survives and keeps its directory, its environment and its REPL. With nothing running there
is nothing to interrupt, and `SIGTERM` / `SIGHUP` / `SIGKILL` are refused in that state
rather than quietly killing the shell.

Ending a terminal is `close`. The two are different intentions and are two actions for
that reason.

## Closing is not optional housekeeping

A terminal left open is a live shell for the rest of the run, and one that is running
something is still running it. Close it when the work that needed its state is done — not
between commands, which would throw away the reason you opened it.

`list` before opening another: a terminal you forgot about is still holding a shell.

## Long-running commands

`send` with `run_in_background: true` types the command and returns at once, watching it as
a **job** — the same registry `bash_tool`'s background commands use, so `job_list_tool`
gives one answer to "what is still outstanding".

`job_kill_tool` stops *watching*. It does not stop the command; that is `signal`.

## Actions

| Action | What it does |
|---|---|
| `open` | Start a terminal, get its id. `command` runs a program instead of a shell. |
| `send` | Type into it, wait for quiet, return **what changed** — not the whole screen. |
| `read` | Look without typing: scrollback, and terminals printing on their own. |
| `signal` | ctrl-C, to the running command. |
| `close` | End it, and everything it started. |
| `list` | Every terminal you opened, with its label and whether it is alive. |

</environment_terminal>
