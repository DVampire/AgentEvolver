---
name: environment_default_ssh
description: "A remote machine as an ECP environment, reached over one multiplexed SSH connection. `ENVIRONMENT.md` is the machine-readable registration document; `environment.py` defines the actions and `service.py` owns the transport, the path boundary and the persistent shell."
version: 1.0.0
type: module
category: environment
requirements: []
metadata: {}
---
# SSH environment

A remote machine the agent *operates*, reached over one multiplexed SSH connection.
`ENVIRONMENT.md` is the machine-readable registration document; `environment.py`
defines the actions and `service.py` owns the transport, the path boundary and the
persistent shell.

## Why an environment and not a tool

A **sandbox** answers "where does the agent's own shell run". An **environment**
answers "what peer does the agent act on". Reaching another machine is the second
question, so this is shaped like the browser environment: an action surface plus
observable state, with the transport underneath.

That shape is what keeps `bash_tool` untouched. It stays the local shell with the
same schema the model has always seen, and the remote is a separate, named set of
actions. Nothing has to be told which machine it is on, because the action names
already say.

The alternative — one shell tool with a `host` or `sandbox` argument — makes every
call a routing decision for the model and gives it two filesystems it can silently
confuse. That failure is on record in this repo: a run whose writes landed on the
host while its commands ran in a container "gave the agent an inconsistent view of
its own environment".

## The connection

One `ControlMaster` per session, opened lazily on first use. Two sessions never
share a channel, so ending one cannot disturb the other.

Multiplexing removes the TCP and crypto handshake but **not** the per-channel
server-side session setup — measured on a real login node, `ssh -O check` costs
0.01s against an established master while running `/bin/true` over it still costs
1.2s. So commands do not each get a channel: one long-lived shell is opened and
commands are fed to its stdin, delimited by a printed sentinel that carries the
exit code. That path costs 0.0008s per command, three orders of magnitude less,
and falls back to a fresh channel whenever it cannot be trusted — a hung command,
a shell that died, a sentinel that did not parse, or any request needing a tty.

Each command runs in a subshell, so a `cd` or a variable it sets cannot leak into
the next one.

## The path boundary

`SSHService.resolve` is the whole safety story. Permission decides *whether* the
agent may write; this decides *where*, and it is the one that keeps a mistake
inside a project directory instead of loose on a shared machine. Local tools get
the same guarantee from `check_session_path`; there is no equivalent for a path on
another host, so it lives here.

Resolution is lexical and refuses `..` outright, and containment is compared by
path *segment* — `/home/u/proj-old` starts with `/home/u/proj` as a string and is a
different directory. A leading `~` is refused rather than passed through: unquoted
it expands on the far side, quoted it creates a directory literally named `~`, and
both have happened.

## Jobs

`launch` starts work in a tmux session named `ae-<session>-<job>`, and every
listing and signal filters on that prefix. The agent can therefore only see and
stop what it started — the machine owner's own sessions are invisible to it. On
the host this was built against those were `claude`, `code` and `eval`, which an
unfiltered `tmux kill-session` would have taken with it.

`jobs` reports finished work as well as running work, because tmux cannot: the
session disappears the moment the command exits. Without the log listing an agent
that launches a job, waits, and asks `jobs` sees an empty list, reasonably concludes
the launch failed, and launches it again.

Launched jobs deliberately outlive the conversation. The live view does not — it is
plumbing, not work, and is taken down with the session that asked for it.

## The live view

`live_view` runs `ttyd` on the far side attached read-only to the agent's tmux
session, and tunnels it to the frontend over the SSH connection that is already
open. Three separate locks keep it read-only and private: no `-W` (ttyd is
read-only by default), `tmux attach -r`, and `-i 127.0.0.1` so the only route in is
the tunnel. The remote port is probed rather than fixed, so a second session on the
same host gets its own.
