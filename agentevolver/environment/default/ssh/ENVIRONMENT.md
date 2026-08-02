---
name: remote_host
description: A remote machine reached over SSH — run commands, manage long-running jobs, read and write files, and move data between the two machines.
version: 1.0.0
type: worker
---

<environment_remote_host>

## The two machines

Every action here acts on the **remote** host. Your other tools act on the **local** one.
No action does both, and no argument switches a tool between them. Data crosses only
through `upload` (local → remote) and `download` (remote → local): a local path means
nothing to an action here, and a remote path means nothing to a local tool.

Everything is confined to the configured workspace root. Paths that resolve outside it —
including via `..` — are rejected before anything runs.

## State

A snapshot of the far machine, refreshed every step: hostname and load average, the
workspace root with its git branch and dirty-file count, recently modified files, the
directory's size, GPU occupancy, disk headroom, and your running jobs.

Only jobs *you* started appear. Other sessions on the host belong to whoever owns the
machine; they are invisible here and cannot be signalled.

## Vision

None. This environment reports text.

## Actions

### run
Run a shell command from the workspace root and wait for it.
- command (str): the command.
- timeout (int, optional): seconds, default 60.
- cwd (str, optional): directory relative to the workspace root.
- tty (bool, optional): allocate a terminal — for programs that draw a screen rather than
  print lines. The result is the rendered screen, not the raw byte stream.

Waiting is the point and the limit: if the timeout passes, the command is abandoned but
the remote process keeps running, untracked. Anything that might take longer than a minute
belongs in `launch`.

### launch
Start a long-running command in a named background session that survives this connection,
the turn, and the conversation.
- command (str): the command.
- name (str): a short handle you will use with `jobs`, `logs` and `signal`.
- cwd (str, optional): directory relative to the workspace root.

Output is captured to a log file, so nothing is lost between checks.

### jobs
List the jobs you started — those still running and those that have finished, with their
exit status.

### logs
Read a job's captured output.
- name (str): the job handle.
- tail (int, optional): last N lines, default 200.

### signal
Send a signal to a job.
- name (str): the job handle.
- signal (str, optional): "TERM" (default) or "KILL".

### read
Read a file.
- path (str): relative to the workspace root.
- offset (int, optional), limit (int, optional): line window.

Large files are truncated. On a machine that produces training logs, read a window or
`grep` for what you need rather than pulling back a file whose size you have not checked.

### write
Write a file, creating parent directories as needed.
- path (str), content (str).

### edit
Replace an exact string in a file.
- path (str), old_string (str), new_string (str).

The old string must appear exactly once; an ambiguous match is refused rather than guessed
at, so include enough surrounding text to make it unique.

### list
List a directory.
- path (str, optional): defaults to the workspace root.

### glob
Find files by pattern.
- pattern (str): e.g. `**/*.py`.

### grep
Search file contents.
- pattern (str): regular expression.
- path (str, optional), include (str, optional): where to search and which files.

### remove
Delete a file or directory.
- path (str), recursive (bool, optional).

There is no trash on the far side. Nothing deleted here comes back.

### upload
Copy a local path to the remote host.
- local_path (str), remote_path (str).

### download
Copy a remote path to the local machine.
- remote_path (str), local_path (str).

### gpu
Report GPU model, memory use and utilisation. Empty when the host has none.

### get_state
Return the state snapshot described above.

</environment_remote_host>
