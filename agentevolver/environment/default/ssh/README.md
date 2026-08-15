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

## The actions are the whole interface

Everything remote happens through this environment's actions: `run`, `read`, `write`,
`edit`, `list`, `grep`, `glob`, `remove`, plus host selection, transfer, long-running jobs,
logs and live views.

The ordinary tools stay local. No argument moves one of them to the remote machine, and
data crosses only through `upload` and `download`. The separation is the safety property:
a step that read on one machine and executed on the other would look like a single
coherent task, to the model and to anyone reading the transcript afterwards.

Transfer between worlds is still explicit through `upload` and `download`. The model does
not get a `host` argument on every ordinary tool call; it works in the selected world and
uses environment actions only when it intentionally crosses that boundary.

## Which machines, and who decides

The environment reaches a *set* of machines, not one. Each is a named record — address,
user, port, key path, jump host, and its own workspace root, because a workspace is a
property of the machine you are working on and two machines rarely keep the same project
in the same place.

Two sources. Hosts in a config are what a deployment ships with: reviewed, in version
control, the same for everyone. Hosts added from the frontend are one person's working
set, so they persist to `output/.runtime/ssh_hosts.json` rather than editing a file under
`configs/`. Same name means the same machine and the runtime record wins; a config host
cannot be deleted from the frontend, because that delete would only last until the next
restart and one that silently undoes itself is worse than one that is refused.

No record holds a credential. A key *path* is what `~/.ssh/config` already keeps in plain
text; a password would have to live in a file, and a file is the one place it must never
be. Authentication stays entirely with ssh.

Every action takes an optional `host`. It defaults to the session's active machine, which
is the point: with one machine configured the agent never names a machine at all, and the
argument stays the exception rather than a routing decision on every call. `use_host`
moves the default for a stretch of work; `host=` targets a single call elsewhere. The
alternative — one environment per machine — makes the action names unambiguous and then
hands the model sixteen tools per machine.

Connections are keyed by (session, machine), so a session working across two machines
holds two, and closing one disturbs neither the other machine nor another session.

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
