---
name: terminal
description: "Keeps a shell alive between tool calls, so a directory change, an activated environment, an ssh hop or a REPL survives the call that made it."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Terminal

Keeps a shell alive between tool calls, so a directory change, an activated environment, an
ssh hop or a REPL survives the call that made it.

| Path | Responsibility |
|---|---|
| `types.py` | `Terminal` — one pty, the program holding it, the reader that drains it, and when a send is done waiting |
| `server.py` | `terminal_manager` — opens, tracks, and reaps terminals; owns the registry |

## Why it exists

`bash_tool` starts a new process per call. Everything a command does to its own
environment therefore ends with it: `cd src` is undone by the next call, an activated
virtualenv is gone, `ssh host` connects and disconnects, and a `python`/`gdb`/`psql`
prompt cannot be reached at all — it opens, receives one line, and is killed.

The workaround the agent reaches for is to fold the state into every command:
`cd /project && source .venv/bin/activate && pytest -k thing`. That works until it does
not — anything interactive has no such form, and neither does anything whose state is a
connection rather than a string.

The pty machinery for this already existed: `bash_tool`'s `tty: true` runs a command under
a real terminal, and `utils/terminal.py` renders what a terminal displays. What was
missing was a terminal that outlives one call.

## What it is not

Not a second way to run commands. A command that stands on its own belongs in `bash_tool`,
which costs one call rather than three. This is for work whose state between commands is
the point.

Not durable. Terminals are session-local and die with the process; a shell is not a place
to keep anything that must survive the run.

## The contract

- **A terminal is owned, or it is a leak.** Every one is registered at the moment it is
  opened, and every way it can end — the agent closing it, the session finishing, the
  process exiting — goes through the registry. A pty nothing holds is a live shell with
  no way to name it, read it, or stop it.
- **A running terminal is never evicted.** The per-session cap refuses a new terminal
  rather than reclaiming an old one. Dropping a live shell would kill work nothing
  recorded and nobody asked to stop; the agent is told to close one instead, which is a
  decision it can make and the registry cannot.
- **Silence is evidence, not proof.** A persistent shell never exits, so there is nothing
  to wait for except the terminal going quiet. Every send reports *why* it stopped waiting
  — `idle`, `timeout`, `exited` — because a command that pauses mid-output is
  indistinguishable from one that finished, and an agent told only "here is the output"
  reads a partial result as a complete one.
- **A send returns what it caused.** Not the whole screen: the lines that scrolled away
  since the write plus the part of the screen that changed. A caller handed the whole
  screen cannot tell this command's output from the last one's.
- **Reading does not type.** Output arrives without being asked for — a build, a server, a
  watcher — and an agent that can only look by sending has to disturb a terminal it may
  not want to touch.
- **A signal goes to the command, not the shell.** The foreground process group is what
  ctrl-C would hit, and the shell has to survive to be typed at afterwards. Ending the
  shell is a separate call, because it is a separate intention.
