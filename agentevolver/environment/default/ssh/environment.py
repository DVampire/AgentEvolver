"""Remote host as an ECP environment — a machine the agent operates, not one it lives in.

The distinction is the whole design. A *sandbox* answers "where does the agent's own shell
run"; an *environment* answers "what peer does the agent act on". Reaching another machine
over SSH is the second question, so this is shaped like ``browser_environment``: an action
surface plus observable state, with the transport underneath.

That shape is what keeps ``bash_tool`` untouched. It stays the local shell, with the same
signature and the same schema the model has always seen; the remote is a separate, named
set of actions. Nothing has to be told which machine it is on, because the action names
already say. The alternative — one shell tool with a target argument — makes every call a
routing decision for the model and gives it two filesystems it can silently confuse. The
failure is on record in this repo: a run whose writes landed on the host while its commands
ran in a container "gave the agent an inconsistent view of its own environment".

Completeness matters for the same reason. The remote side carries the whole surface —
execute, read, write, edit, search, transfer, and long-running jobs — so work on the far
machine never has to borrow a local tool and land in the wrong place.
"""

from __future__ import annotations

import os
import shlex
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.environment.default.ssh.service import (
    RemotePathError,
    SSHConfig,
    SSHResult,
    SSHService,
)
from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment, EnvironmentView
from agentevolver.logger import logger
from agentevolver.registry import ENVIRONMENT

#: Commands longer than this belong in `launch`. The ceiling is deliberately low: a
#: synchronous command that outlives its timeout leaves a process running on the far end
#: with nothing tracking it, and the agent has no way to find it again. `launch` gives the
#: same work a name, a log and a handle.
_DEFAULT_RUN_TIMEOUT = 60.0

#: Read cap. A training log is routinely hundreds of megabytes; pulling one into the
#: context window buys nothing and costs the rest of the turn. `grep` and the offset/limit
#: arguments exist so a caller can find the part that matters instead.
_READ_LIMIT_BYTES = 256 * 1024

#: Prefix for every tmux session this environment starts, with the session id folded in.
#: Job listing and signalling both filter on it, so the agent can only ever see and stop
#: what it started — the user's own `tmux` sessions on a shared login node are invisible to
#: it. Observed on the target host: `claude`, `code` and `eval` sessions belonging to the
#: human, which an unfiltered `tmux kill-session` would have taken with it.
_JOB_PREFIX = "ae"


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, **payload}


def _fail(message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "message": message, **extra}


@ENVIRONMENT.register_module(force=True)
class SSHEnvironment(Environment):
    """A remote machine, operated over one persistent SSH connection."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="remote_host")
    description: str = Field(
        default="A remote machine reached over SSH: run commands, manage long-running "
                "jobs, read and write files, and move data in and out."
    )
    metadata: Dict[str, Any] = Field(default={"has_vision": False})
    enable_evolving: bool = Field(default=False)

    def __init__(
        self,
        host: str = "",
        user: str = "",
        port: int = 22,
        identity_file: str = "",
        jump_host: str = "",
        workspace_root: str = "~",
        connect_timeout: int = 15,
        known_hosts_strict: bool = True,
        #: Whether the agent may start work that outlives the turn. Kept separate from the
        #: permission mode because it is the only action here with that property: a
        #: launched job keeps consuming GPUs and disk after the conversation has moved on,
        #: and nothing in the session's lifecycle stops it.
        allow_launch: bool = True,
        max_upload_mb: int = 500,
        live_view: bool = True,
        state_entries: int = 20,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._config = SSHConfig(
            host=host,
            user=user,
            port=port,
            identity_file=identity_file,
            jump_host=jump_host,
            workspace_root=workspace_root,
            connect_timeout=connect_timeout,
            known_hosts_strict=known_hosts_strict,
        )
        self._allow_launch = allow_launch
        self._max_upload_mb = max_upload_mb
        self._live_view_enabled = live_view
        self._state_entries = state_entries
        self._services: Dict[str, SSHService] = {}
        self._last: Dict[str, Dict[str, Any]] = {}
        self._view_port: Optional[int] = None
        self._view_remote_ports: Dict[str, int] = {}
        self._view_urls: Dict[str, str] = {}

    # ------------------------------------------------------------------ session
    @staticmethod
    def _session_id(ctx) -> str:
        return (getattr(ctx, "id", "") or "default") if ctx else "default"

    async def _svc(self, ctx) -> SSHService:
        """The connection for this session, opened on first use.

        One master per session rather than one per environment: two conversations must not
        share a channel, and ending one must not disturb the other.
        """
        sid = self._session_id(ctx)
        service = self._services.get(sid)
        if service is None or not await service.is_alive():
            service = SSHService(self._config, sid)
            await service.start()
            self._services[sid] = service
        return service

    def _job_prefix(self, ctx) -> str:
        return f"{_JOB_PREFIX}-{self._session_id(ctx)[:8]}-"

    async def initialize(self) -> None:
        logger.info(f"| 🔌 SSH environment ready: {self._config.target} (lazy connect)")

    async def cleanup(self) -> None:
        for sid in list(self._services):
            await self.close_session(sid)
        self._services.clear()

    async def close_session(self, session_id: str) -> None:
        service = self._services.pop(session_id, None)
        if service is None:
            return
        try:
            await self._stop_view(service, session_id)
        except Exception as exc:  # noqa: BLE001 — teardown must not raise
            logger.warning(f"| ⚠️ SSH view teardown: {exc}")
        try:
            await service.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"| ⚠️ SSH teardown: {exc}")

    async def _stop_view(self, service: SSHService, session_id: str) -> None:
        """Take the view's server down with the session that asked for it.

        Launched jobs deliberately outlive the conversation — that is what `launch` is for.
        The view is not work, it is plumbing, and leaving it behind would accumulate an
        idle ttyd and two tmux sessions on the far host for every run ever started.
        """
        self._view_remote_ports.pop(session_id, None)
        self._view_urls.pop(session_id, None)
        view = f"{_JOB_PREFIX}-{session_id[:8]}-view"
        await service.run_raw(
            f"tmux kill-session -t {shlex.quote(view + '-srv')} 2>/dev/null; "
            f"tmux kill-session -t {shlex.quote(view)} 2>/dev/null; true",
            timeout=20,
        )

    def _record(self, ctx, command: str, result: SSHResult) -> None:
        self._last[self._session_id(ctx)] = {
            "command": command,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }

    # ------------------------------------------------------------------ execute
    @environment_manager.action(
        name="run",
        description="Run a shell command on the remote host, from the workspace root. "
                    "For anything that may run longer than a minute — training, large "
                    "downloads, data processing — use `launch` instead: this waits, and "
                    "giving up waiting does not stop the remote process.",
    )
    async def run(
        self,
        command: str,
        timeout: int = 60,
        cwd: str = "",
        tty: bool = False,
        ctx=None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
            result = await service.run(
                command,
                timeout=min(float(timeout or _DEFAULT_RUN_TIMEOUT), 900.0),
                cwd=cwd or None,
                tty=bool(tty),
            )
        except RemotePathError as exc:
            return _fail(str(exc))
        except ConnectionError as exc:
            return _fail(f"ssh: {exc}")

        self._record(ctx, command, result)
        payload: Dict[str, Any] = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }
        if result.screen is not None:
            payload["screen"] = result.screen
        else:
            payload["stdout"] = result.stdout
            if result.stderr:
                payload["stderr"] = result.stderr
        # A non-zero exit is an observation, not a tool failure — `grep` returns 1 when it
        # finds nothing. Same contract the local shell tool states.
        return _ok(payload)

    # ------------------------------------------------------------------ files
    @environment_manager.action(
        name="read",
        description="Read a text file on the remote host. Use offset/limit for large "
                    "files rather than pulling the whole thing back.",
    )
    async def read(
        self, path: str, offset: int = 0, limit: int = 0, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
            target = service.resolve(path)
        except RemotePathError as exc:
            return _fail(str(exc))

        if offset or limit:
            start = max(1, int(offset) + 1)
            count = int(limit) or 2000
            command = f"sed -n {start},{start + count - 1}p {shlex.quote(target)}"
        else:
            command = f"head -c {_READ_LIMIT_BYTES + 1} {shlex.quote(target)}"

        result = await service.run(command, timeout=120)
        if not result.ok:
            return _fail(f"read {path!r}: {(result.stderr or result.stdout).strip()[:300]}")

        content = result.stdout
        truncated = len(content.encode()) > _READ_LIMIT_BYTES
        if truncated:
            content = content[:_READ_LIMIT_BYTES]
        return _ok({
            "path": target,
            "content": content,
            "truncated": truncated,
            **({"note": f"truncated at {_READ_LIMIT_BYTES // 1024}KB — use offset/limit or grep"}
               if truncated else {}),
        })

    @environment_manager.action(
        name="write",
        description="Write a text file on the remote host, creating parent directories. "
                    "Replaces the whole file; use `edit` to change part of one.",
    )
    async def write(self, path: str, content: str, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        import base64

        try:
            service = await self._svc(ctx)
            target = service.resolve(path)
        except RemotePathError as exc:
            return _fail(str(exc))

        # base64 rather than a heredoc: the content is arbitrary, and every delimiter a
        # heredoc could use is something a file may legitimately contain.
        encoded = base64.b64encode(content.encode()).decode()
        parent = os.path.dirname(target) or "/"
        command = (
            f"mkdir -p {shlex.quote(parent)} && "
            f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(target)}"
        )
        result = await service.run(command, timeout=300)
        if not result.ok:
            return _fail(f"write {path!r}: {(result.stderr or result.stdout).strip()[:300]}")
        return _ok({"path": target, "bytes": len(content.encode())})

    @environment_manager.action(
        name="edit",
        description="Replace an exact string in a remote file. Fails unless the string "
                    "occurs exactly once, so a near-miss cannot silently change the wrong "
                    "line.",
    )
    async def edit(
        self, path: str, old: str, new: str, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        read_result = await self.read(path, ctx=ctx)
        if not read_result.get("success"):
            return read_result
        content = read_result["content"]
        if read_result.get("truncated"):
            return _fail(
                f"{path!r} is too large to edit safely — it was truncated on read, so a "
                f"replacement could not be verified as unique. Use `run` with sed for a "
                f"targeted change."
            )
        occurrences = content.count(old)
        if occurrences == 0:
            return _fail(f"{old!r} not found in {path!r}")
        if occurrences > 1:
            return _fail(
                f"{old!r} occurs {occurrences} times in {path!r} — include enough "
                f"surrounding text to make it unique"
            )
        return await self.write(path, content.replace(old, new, 1), ctx=ctx)

    @environment_manager.action(
        name="list",
        description="List a remote directory. Depth 1 by default; a deep listing of a "
                    "results tree is thousands of lines that say nothing.",
    )
    async def list(
        self, path: str = "", depth: int = 1, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
            target = service.resolve(path)
        except RemotePathError as exc:
            return _fail(str(exc))

        command = (
            f"find {shlex.quote(target)} -maxdepth {max(1, int(depth))} -mindepth 1 "
            f"-printf '%y\\t%s\\t%TY-%Tm-%Td %TH:%TM\\t%p\\n' 2>/dev/null | head -400"
        )
        result = await service.run(command, timeout=60)
        entries = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4:
                kind, size, modified, full = parts
                entries.append({
                    "type": "dir" if kind == "d" else "file",
                    "size": int(size) if size.isdigit() else 0,
                    "modified": modified,
                    "path": full,
                })
        return _ok({"path": target, "entries": entries, "count": len(entries)})

    @environment_manager.action(
        name="grep",
        description="Search remote file contents with a regex. Do this rather than "
                    "reading files back to search them locally.",
    )
    async def grep(
        self, pattern: str, path: str = "", glob: str = "", max_results: int = 100,
        ctx=None, **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
            target = service.resolve(path)
        except RemotePathError as exc:
            return _fail(str(exc))

        include = f"--include={shlex.quote(glob)} " if glob else ""
        command = (
            f"grep -rnI {include}-e {shlex.quote(pattern)} {shlex.quote(target)} "
            f"2>/dev/null | head -{max(1, int(max_results))}"
        )
        result = await service.run(command, timeout=120)
        matches = [line for line in result.stdout.splitlines() if line.strip()]
        # grep exits 1 on "no matches" — a result, not an error.
        return _ok({"pattern": pattern, "matches": matches, "count": len(matches)})

    @environment_manager.action(
        name="glob",
        description="Find remote files by name pattern, newest first.",
    )
    async def glob(
        self, pattern: str, path: str = "", max_results: int = 100, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
            target = service.resolve(path)
        except RemotePathError as exc:
            return _fail(str(exc))

        command = (
            f"find {shlex.quote(target)} -name {shlex.quote(pattern)} -printf '%T@\\t%p\\n' "
            f"2>/dev/null | sort -rn | head -{max(1, int(max_results))} | cut -f2"
        )
        result = await service.run(command, timeout=120)
        files = [line for line in result.stdout.splitlines() if line.strip()]
        return _ok({"pattern": pattern, "files": files, "count": len(files)})

    @environment_manager.action(
        name="remove",
        description="Delete a remote file or directory inside the workspace.",
    )
    async def remove(
        self, path: str, recursive: bool = False, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
            target = service.resolve(path)
        except RemotePathError as exc:
            return _fail(str(exc))
        if target.rstrip("/") == service.workspace_root.rstrip("/"):
            return _fail("refusing to delete the workspace root itself")

        flag = "-rf" if recursive else "-f"
        result = await service.run(f"rm {flag} {shlex.quote(target)}", timeout=120)
        if not result.ok:
            return _fail(f"remove {path!r}: {(result.stderr or result.stdout).strip()[:200]}")
        return _ok({"path": target, "recursive": bool(recursive)})

    # ------------------------------------------------------------------ transfer
    @environment_manager.action(
        name="upload",
        description="Copy a local file or directory to the remote workspace.",
    )
    async def upload(
        self, local_path: str, remote_path: str, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        source = os.path.abspath(os.path.expanduser(local_path))
        if not os.path.exists(source):
            return _fail(f"local path does not exist: {local_path}")

        size_mb = _tree_size_mb(source)
        if size_mb > self._max_upload_mb:
            return _fail(
                f"{local_path} is {size_mb:.0f} MB, over the {self._max_upload_mb} MB "
                f"limit for this host"
            )
        try:
            service = await self._svc(ctx)
            destination = service.remote_spec(remote_path)
        except RemotePathError as exc:
            return _fail(str(exc))

        result = await service.rsync(source, destination)
        if not result.ok:
            return _fail(f"upload failed: {(result.stderr or result.stdout).strip()[:300]}")
        return _ok({"local": source, "remote": destination, "megabytes": round(size_mb, 2)})

    @environment_manager.action(
        name="download",
        description="Copy a remote file or directory back to the local machine.",
    )
    async def download(
        self, remote_path: str, local_path: str, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
            source = service.remote_spec(remote_path)
        except RemotePathError as exc:
            return _fail(str(exc))

        destination = os.path.abspath(os.path.expanduser(local_path))
        # The remote side of this environment is boundary-checked; the local side has to
        # be too, or `download` becomes the one action that writes anywhere on this
        # machine. It is the same check every local file tool applies — a session that
        # carries sandbox roots is held to them, and one that does not keeps the legacy
        # behaviour.
        denial = _local_write_denied(ctx, destination)
        if denial:
            return _fail(denial)
        os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
        result = await service.rsync(source, destination)
        if not result.ok:
            return _fail(f"download failed: {(result.stderr or result.stdout).strip()[:300]}")
        return _ok({"remote": source, "local": destination})

    # ------------------------------------------------------------------ jobs
    @environment_manager.action(
        name="launch",
        description="Start a long-running command that survives this turn — training, a "
                    "large download, data processing. Returns immediately with a job "
                    "name; follow it with `logs` and stop it with `signal`.",
    )
    async def launch(
        self, command: str, name: str, gpus: str = "", ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        if not self._allow_launch:
            return _fail("launching background work is disabled for this host")
        safe = "".join(ch for ch in (name or "job") if ch.isalnum() or ch in "-_")[:32] or "job"
        try:
            service = await self._svc(ctx)
        except ConnectionError as exc:
            return _fail(f"ssh: {exc}")

        session = f"{self._job_prefix(ctx)}{safe}"
        log_dir = f"{service.workspace_root}/.agentevolver/logs"
        log_path = f"{log_dir}/{safe}.log"

        exists = await service.run(
            f"tmux has-session -t {shlex.quote(session)} 2>/dev/null && echo yes || echo no",
            timeout=30,
        )
        if exists.stdout.strip() == "yes":
            return _fail(
                f"job {safe!r} is already running — stop it with signal, or pick another "
                f"name",
                job=safe,
            )

        env_prefix = f"CUDA_VISIBLE_DEVICES={shlex.quote(gpus)} " if gpus else ""
        # tmux rather than nohup: the job survives the connection either way, but a tmux
        # session can be attached to afterwards, by the agent for a live look and by a
        # person to take over. `tee` as well as the pane, because scrollback is bounded and
        # a training run overruns it in minutes.
        inner = (
            f"cd {shlex.quote(service.workspace_root)} && mkdir -p {shlex.quote(log_dir)} && "
            f"{env_prefix}{command} 2>&1 | tee {shlex.quote(log_path)}"
        )
        result = await service.run(
            f"tmux new-session -d -s {shlex.quote(session)} {shlex.quote(inner)}", timeout=60
        )
        if not result.ok:
            return _fail(f"launch failed: {(result.stderr or result.stdout).strip()[:300]}")

        logger.info(f"| 🚀 launched {session} on {self._config.target}")
        return _ok({
            "job": safe,
            "session": session,
            "log": log_path,
            "gpus": gpus or "(unset — inherits whatever is visible)",
            "attach": f"tmux attach -t {session}",
        })

    @environment_manager.action(
        name="jobs",
        description="List this session's long-running jobs, running and finished. A "
                    "finished job keeps its log — read it with `logs`.",
    )
    async def jobs(self, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        service = await self._svc(ctx)
        prefix = self._job_prefix(ctx)
        log_dir = f"{service.workspace_root}/.agentevolver/logs"

        # Both halves in one round trip. A finished job is not the same as one that never
        # existed, and tmux cannot tell them apart: the session goes away the moment the
        # command exits. Without the log listing, an agent that launches a job, waits, and
        # asks `jobs` sees an empty list and reasonably concludes the launch failed — then
        # launches it again. The log file is what survives, so it is what proves the job
        # ran.
        result = await service.run(
            "tmux list-sessions -F '#{session_name}\t#{session_attached}' 2>/dev/null || true; "
            f"echo '__LOGS__'; "
            f"ls -1t {shlex.quote(log_dir)}/*.log 2>/dev/null | head -20 || true",
            timeout=30,
        )
        sessions_part, _, logs_part = result.stdout.partition("__LOGS__")

        running: Dict[str, Dict[str, Any]] = {}
        for line in sessions_part.splitlines():
            parts = line.split("\t")
            # Only this session's jobs. The target host carries the user's own tmux
            # sessions — on the machine this was built against, `claude`, `code` and
            # `eval` — which are none of the agent's business and must not be listable,
            # let alone killable.
            if parts and parts[0].startswith(prefix):
                job = parts[0][len(prefix):]
                running[job] = {
                    "job": job,
                    "session": parts[0],
                    "state": "running",
                    "attached": parts[1] == "1" if len(parts) > 1 else False,
                }

        finished = []
        for line in logs_part.splitlines():
            name = os.path.basename(line.strip())
            if not name.endswith(".log"):
                continue
            job = name[:-4]
            if job and job not in running and job != "view":
                finished.append({"job": job, "state": "finished", "log": line.strip()})

        jobs = list(running.values()) + finished
        return _ok({"jobs": jobs, "count": len(jobs),
                    "running": len(running), "finished": len(finished)})

    @environment_manager.action(
        name="logs",
        description="Read the tail of a launched job's log.",
    )
    async def logs(
        self, job: str, lines: int = 100, ctx=None, **kwargs: Any
    ) -> Dict[str, Any]:
        service = await self._svc(ctx)
        safe = "".join(ch for ch in (job or "") if ch.isalnum() or ch in "-_")[:32]
        log_path = f"{service.workspace_root}/.agentevolver/logs/{safe}.log"
        result = await service.run(
            f"tail -n {max(1, int(lines))} {shlex.quote(log_path)} 2>/dev/null || "
            f"echo '(no log yet for {safe})'",
            timeout=60,
        )
        return _ok({"job": safe, "log": log_path, "tail": result.stdout})

    @environment_manager.action(
        name="signal",
        description="Stop a job this session launched.",
    )
    async def signal(self, job: str, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        service = await self._svc(ctx)
        safe = "".join(ch for ch in (job or "") if ch.isalnum() or ch in "-_")[:32]
        session = f"{self._job_prefix(ctx)}{safe}"
        result = await service.run(
            f"tmux kill-session -t {shlex.quote(session)} 2>/dev/null && echo killed || echo absent",
            timeout=30,
        )
        state = result.stdout.strip()
        if state != "killed":
            return _fail(f"no job named {safe!r} in this session", job=safe)
        return _ok({"job": safe, "stopped": True})

    # ------------------------------------------------------------------ state
    @environment_manager.action(
        name="get_state",
        description="What the remote workspace looks like right now: files, git status, "
                    "GPU occupancy, running jobs, and the last command's exit code.",
    )
    async def get_state(self, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        try:
            service = await self._svc(ctx)
        except ConnectionError as exc:
            return _fail(f"ssh: {exc}")

        root = service.workspace_root
        # One round trip for the whole picture. Split across calls this would be six, and
        # state is read often enough that the difference is felt.
        probe = f"""
cd {shlex.quote(root)} 2>/dev/null || exit 1
echo "__HOST__"; uname -sr; hostname
echo "__GIT__"; git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(not a git repo)"
git status --porcelain 2>/dev/null | head -20
echo "__FILES__"
find . -maxdepth 1 -mindepth 1 -printf '%y\\t%s\\t%TY-%Tm-%Td %TH:%TM\\t%f\\n' 2>/dev/null \
  | sort -k4 | head -{self._state_entries}
echo "__DIRSIZE__"; du -sh . 2>/dev/null | cut -f1
echo "__GPU__"
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null || echo "(no nvidia-smi)"
echo "__DISK__"; df -h . 2>/dev/null | tail -1 | awk '{{print $5" used of "$2}}'
echo "__JOBS__"
tmux list-sessions -F '#{{session_name}}' 2>/dev/null | grep {shlex.quote(self._job_prefix(ctx))} || true
"""
        result = await service.run_raw(
            f"bash -s <<'__AE_PROBE__'\n{probe}\n__AE_PROBE__", timeout=60
        )
        sections = _split_sections(result.stdout)
        last = self._last.get(self._session_id(ctx))

        info = _render_state(
            target=self._config.target,
            root=root,
            sections=sections,
            last=last,
            job_prefix=self._job_prefix(ctx),
        )
        return _ok({
            "info": info,
            "workspace_root": root,
            "host": self._config.target,
            "raw": sections,
        })

    @environment_manager.action(
        name="gpu",
        description="Full GPU detail on the remote host — who is using what.",
    )
    async def gpu(self, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        service = await self._svc(ctx)
        result = await service.run_raw(
            "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu "
            "--format=csv,noheader 2>/dev/null || echo '(no nvidia-smi)'",
            timeout=30,
        )
        return _ok({"gpus": result.stdout.strip()})

    # ------------------------------------------------------------------ live view
    async def live_view(self, ctx=None) -> Optional[EnvironmentView]:
        """A read-only terminal onto the remote session, for the frontend to embed.

        Same contract the browser environment's noVNC view has: the descriptor says where
        to look, and the stream goes browser ↔ endpoint without passing through the agent
        or the gateway.
        """
        if not self._live_view_enabled:
            return None
        try:
            url = await self._ensure_view(ctx)
        except Exception as exc:  # noqa: BLE001 — a missing view must not break the run
            logger.warning(f"| ⚠️ SSH live view unavailable: {exc}")
            return None
        return EnvironmentView(type="iframe", url=url) if url else None

    async def _ensure_view(self, ctx) -> Optional[str]:
        from agentevolver.port import port_manager

        sid = self._session_id(ctx)
        # The manager announces the view after *every* action. For the browser that is a
        # property lookup; here the full path is five remote round trips and a subprocess,
        # which turned a one-second action into a nine-second one. Once the view is up its
        # address does not change, so the answer is remembered and the work is done once.
        cached = self._view_urls.get(sid)
        if cached:
            return cached

        service = await self._svc(ctx)

        # ttyd is a single static binary and the user may not have root on the far end, so
        # it goes in ~/.local/bin. Checked before fetching so a host that already has it —
        # or has no internet — is not disturbed.
        # The absolute path is captured, not composed. Anything handed to tmux is quoted
        # before it gets there, and `$HOME` inside single quotes is a literal — the same
        # trap that once made `~/proj` resolve to a directory actually named `~`.
        probe = await service.run_raw(
            'command -v ttyd || (test -x "$HOME/.local/bin/ttyd" && echo "$HOME/.local/bin/ttyd") '
            '|| echo MISSING',
            timeout=30,
        )
        ttyd_path = probe.stdout.strip().splitlines()[-1] if probe.stdout.strip() else "MISSING"
        if ttyd_path == "MISSING":
            logger.info(f"| ⬇️  installing ttyd on {self._config.target}")
            install = await service.run_raw(
                'mkdir -p "$HOME/.local/bin" && '
                'curl -fsSL -o "$HOME/.local/bin/ttyd" '
                'https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.x86_64 && '
                'chmod +x "$HOME/.local/bin/ttyd" && echo "$HOME/.local/bin/ttyd"',
                timeout=180,
            )
            if not install.ok:
                return None
            ttyd_path = install.stdout.strip().splitlines()[-1]

        # A fixed remote port would let the first session on a host claim it and leave every
        # later one unable to bind — and the loser's only symptom is a view that never
        # appears. The far end picks a free one instead, and it is remembered so asking for
        # the view twice reuses the server rather than starting a second.
        remote_port = self._view_remote_ports.get(sid)
        if remote_port is None:
            picked = await service.run_raw(
                "for p in $(seq 7681 7780); do "
                '(ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -q ":$p " '
                "|| { echo $p; break; }; done",
                timeout=20,
            )
            candidate = picked.stdout.strip().splitlines()[-1] if picked.stdout.strip() else ""
            if not candidate.isdigit():
                logger.warning(f"| ⚠️ no free port for the view on {self._config.target}")
                return None
            remote_port = int(candidate)
            self._view_remote_ports[sid] = remote_port
        session_name = f"{self._job_prefix(ctx)}view"
        await service.run_raw(
            f'tmux has-session -t {shlex.quote(session_name)} 2>/dev/null || '
            f'tmux new-session -d -s {shlex.quote(session_name)} '
            f'-c {shlex.quote(service.workspace_root)}',
            timeout=30,
        )
        # ttyd serves read-only by default — `-W` would be the flag to allow writing, and
        # it is deliberately absent. `attach -r` locks the tmux side too. Two locks because
        # the view exists to watch the agent work, and a human typing into the same pane
        # would be fighting it for the keyboard.
        #
        # `-i 127.0.0.1` is the third lock, and the one that matters most on a shared login
        # node: ttyd binds every interface by default, which would put an unauthenticated
        # terminal onto the network for anyone who can reach the host. The view is meant to
        # arrive through the ssh tunnel, and now that is the only way it can.
        #
        # No `-o`: that flag means "serve one client and exit on disconnect", which would
        # make the view work exactly once. And ttyd runs inside its own tmux session rather
        # than under `nohup &` — backgrounding it from a command that ssh is about to close
        # leaves it in that command's process group, and it goes away with the connection.
        # A tmux session is the same detachment the launched jobs already rely on.
        #
        # The already-running check asks tmux, not `pgrep -f`: a `pgrep` pattern travels
        # inside the very `bash -c` string being searched, so it matches its own shell and
        # reports a server that was never started. tmux is exact, and because a session
        # whose command exits disappears with it, presence here means ttyd is actually up.
        server_session = f"{session_name}-srv"
        await service.run_raw(
            f'tmux has-session -t {shlex.quote(server_session)} 2>/dev/null || '
            f'tmux new-session -d -s {shlex.quote(server_session)} '
            f'{shlex.quote(f"{ttyd_path} -i 127.0.0.1 -p {remote_port} tmux attach -r -t {session_name}")}',
            timeout=30,
        )
        # ttyd needs a moment to bind before the tunnel is worth opening.
        import asyncio as _aio

        for _ in range(10):
            probe = await service.run_raw(
                f"(ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -q ':{remote_port} ' "
                f"&& echo up || echo down",
                timeout=15,
            )
            if probe.stdout.strip() == "up":
                break
            await _aio.sleep(0.5)
        else:
            logger.warning(f"| ⚠️ ttyd did not bind {remote_port} on {self._config.target}")
            return None

        if self._view_port is None:
            record = port_manager.register(f"ssh-view:{sid}", type="host")
            self._view_port = record["port"]

        # `-O forward` asks the running master to add the tunnel, so watching costs no
        # extra connection. It has to be *waited on*: the URL is only usable once the
        # local listener exists, and returning before then hands the frontend an address
        # that refuses the connection it is about to make.
        proc = await _aio.create_subprocess_exec(
            *service._base_args(),  # noqa: SLF001 — same package, one transport
            "-O", "forward", "-L", f"{self._view_port}:127.0.0.1:{remote_port}",
            self._config.target,
            stdout=_aio.subprocess.PIPE, stderr=_aio.subprocess.PIPE,
        )
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            # A repeat request for a forward the master already holds is not a failure.
            detail = err.decode(errors="replace").strip()
            if "forward" not in detail.lower():
                logger.warning(f"| ⚠️ SSH view tunnel failed: {detail}")
                return None
        url = f"http://127.0.0.1:{self._view_port}/"
        self._view_urls[sid] = url
        return url


# ---------------------------------------------------------------------- helpers
def _local_write_denied(ctx, destination: str) -> Optional[str]:
    """Denial reason when writing `destination` would leave this session's local roots."""
    try:
        from agentevolver.sandbox.project import check_session_path
    except ImportError:  # pragma: no cover — sandbox module always ships
        return None
    return check_session_path(ctx, destination, write=True)


def _tree_size_mb(path: str) -> float:
    if os.path.isfile(path):
        return os.path.getsize(path) / 1024 / 1024
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total / 1024 / 1024


def _split_sections(raw: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {}
    current = "preamble"
    for line in raw.splitlines():
        if line.startswith("__") and line.endswith("__"):
            current = line.strip("_").lower()
            sections[current] = []
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _render_state(*, target: str, root: str, sections: Dict[str, List[str]],
                  last: Optional[Dict[str, Any]], job_prefix: str) -> str:
    """The observation the agent reads before deciding what to do next.

    Written as prose rather than JSON because it is read by a model in a prompt, and the
    same reasoning the browser environment applies to its `<info>` block applies here.

    The GPU line is not decoration. On the host this was built against all four cards sat
    at 88.5 of 95.8 GB, and an agent that starts a training run without looking spends an
    hour to arrive at an out-of-memory error.
    """
    host_lines = [line for line in sections.get("host", []) if line.strip()]
    git_lines = [line for line in sections.get("git", []) if line.strip()]
    file_lines = [line for line in sections.get("files", []) if line.strip()]
    gpu_lines = [line for line in sections.get("gpu", []) if line.strip()]
    disk = " ".join(sections.get("disk", [])).strip()
    dirsize = " ".join(sections.get("dirsize", [])).strip()
    job_lines = [line.strip() for line in sections.get("jobs", []) if line.strip()]

    out = ["<info>"]
    out.append(f"Host: {target}" + (f" ({host_lines[0]})" if host_lines else ""))

    branch = git_lines[0] if git_lines else "(unknown)"
    dirty = len(git_lines) - 1
    out.append(f"Workspace: {root}   [git: {branch}"
               + (f", {dirty} changed" if dirty > 0 else ", clean") + "]")
    if dirsize:
        out.append(f"Size: {dirsize}" + (f"   Disk: {disk}" if disk else ""))

    if file_lines:
        out.append(f"\nFiles (depth 1, {len(file_lines)} shown):")
        for line in file_lines:
            parts = line.split("\t", 3)
            if len(parts) == 4:
                kind, size, modified, name = parts
                marker = "/" if kind == "d" else " "
                out.append(f"  {name}{marker}".ljust(34) + f"{_human(size)}  {modified}")

    if gpu_lines:
        out.append("\nGPU:")
        for line in gpu_lines:
            out.append(f"  {line.strip()}")

    if job_lines:
        out.append("\nJobs (this session):")
        for line in job_lines:
            out.append(f"  {line[len(job_prefix):] if line.startswith(job_prefix) else line}")
    else:
        out.append("\nJobs: none running")

    if last:
        verdict = "timed out" if last.get("timed_out") else f"exit {last.get('exit_code')}"
        out.append(f"\nLast command: {str(last.get('command'))[:80]!r} → {verdict}")

    out.append("</info>")
    return "\n".join(out)


def _human(size: str) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return size
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return size
