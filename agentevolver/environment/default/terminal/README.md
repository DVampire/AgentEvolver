---
name: environment_default_terminal
description: "Terminals that outlive a call, as an ECP environment. `ENVIRONMENT.md` is the registration document; `environment.py` defines the six actions over `agentevolver.terminal`, which owns the pty."
version: 1.0.0
type: module
category: environment
requirements: []
metadata: {}
---
# Terminal environment

A shell that stays where it was left. `ENVIRONMENT.md` is the machine-readable
registration document; `environment.py` defines the actions. The pty itself, the reader
thread and the reaping all live in `agentevolver.terminal` and are unchanged.

| Path | Responsibility |
|---|---|
| `ENVIRONMENT.md` | Registration document — what it is for, and the rules an agent reads |
| `environment.py` | The six actions, and the state every step renders |

## Why an environment and not six tools

It was six tools, and the giveaway was `terminal_read_tool`: a tool whose whole job was to
fetch state the agent should already have been looking at. A tool cannot volunteer
anything — the agent has to remember to call it — and an agent that forgets is typing into
a shell it has not seen since two commands ago.

As an environment, `get_state` renders every live terminal into `environment-state` each
step. `read` goes back to being what it is for: scrollback, and terminals producing output
on their own between steps.

The three relations in the capability table say the same thing. A capability is something
an agent *uses*; an environment is something it is *in*. A pty holding a directory, a
virtualenv, an ssh session and a half-finished REPL is the second kind.

## What the actions cost

`open` waits for the shell's first prompt before returning, so a startup banner — or an
ssh password prompt — does not land in the middle of the first command's output.

`send` returns **what changed**, not the whole screen, and a wait reason: `idle`,
`timeout` or `exited`. A terminal has nothing that says "the command finished", so silence
is the only available evidence, and `idle` on a command paused mid-way looks exactly like
`idle` on one that is done. That distinction is written into the action's description
rather than left to the reader, because it is the one thing an agent gets wrong here.

`signal` goes to the running command, not the shell — the terminal survives. `close` ends
it and everything it started, which is not optional: a terminal left open is a live shell
for the rest of the run.

## State size

Every open terminal is rendered every step, so the cost is per terminal per step.
`STATE_LINES_PER_TERMINAL` bounds each one at the live tail — enough to see what a command
printed, not enough for four terminals to crowd out the conversation. Closed terminals are
not rendered at all; their output is still reachable through `read`.
