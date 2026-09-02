"""SSH environment: the path boundary, the transport's argument building, and the actions.

Everything here runs without a remote host. The parts that genuinely need one — that a
command's output comes back intact, that a launched job survives the connection — are in
the gated suite at the bottom, which runs only when ``AE_SSH_TEST_HOST`` names a machine.

The bias is deliberate. What can be tested offline is what *breaks* offline: the boundary
check that keeps the agent inside its workspace, and the quoting that decides which file a
path names. Both have already been wrong in this module once — a `~` that became a
directory literally named `~`, and a `pgrep` pattern that matched the shell carrying it.

Grouped into classes rather than banner sections: each `Test…` class is one claim about
the environment and its docstring says which, so a failure names its own subject before
anyone opens the file.
"""

import asyncio
import json
import os
import shlex
from typing import Dict, List
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agentevolver.environment.default.ssh.service import (
    PurePosixPathish,
    RemotePathError,
    SSHConfig,
    SSHResult,
    SSHService,
)


def _service(root: str = "/home/u/proj", **kwargs) -> SSHService:
    service = SSHService(SSHConfig(host="example", user="u", workspace_root=root, **kwargs), "s1")
    service._resolved_root = root
    return service


# --------------------------------------------------------------------- path boundary
class TestPathBoundary:
    """`resolve` is the only thing standing between the agent and the rest of the host."""

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("", "/home/u/proj"),
            ("a.txt", "/home/u/proj/a.txt"),
            ("./a.txt", "/home/u/proj/a.txt"),
            ("sub/../a.txt", "/home/u/proj/a.txt"),
            ("sub//nested///b", "/home/u/proj/sub/nested/b"),
            ("/home/u/proj/deep/c", "/home/u/proj/deep/c"),
            ("dir\\file", "/home/u/proj/dir/file"),
        ],
    )
    def test_paths_inside_the_root_resolve(self, given: str, expected: str) -> None:
        """Every way a model writes a path inside the workspace lands in the same place.

        The last case is not hypothetical: a model that has read Windows paths writes
        them, and a backslash left alone becomes part of the filename instead of a
        separator — creating a file whose name nothing else can refer to.
        """
        assert _service().resolve(given) == expected

    @pytest.mark.parametrize(
        "escape",
        [
            "../secrets",
            "a/../../secrets",
            "/etc/passwd",
            "/home/u/other",
            "../../../../etc/shadow",
            "a/b/../../../c",
        ],
    )
    def test_paths_that_escape_are_refused(self, escape: str) -> None:
        """Six spellings of "somewhere else", and the refusal must not depend on which
        one was used. Absolute and relative escapes take different routes through the
        same function, and a check that normalises only one of them lets the other reach
        a real file on a machine the agent was lent, not given.
        """
        with pytest.raises(RemotePathError):
            _service().resolve(escape)

    def test_a_sibling_with_the_root_as_a_string_prefix_is_not_inside_it(self) -> None:
        """`/home/u/proj-old` starts with `/home/u/proj` and is a different directory.

        The comparison is by path segment for exactly this case; a `startswith` check
        would have let the agent write to the neighbouring directory.
        """
        with pytest.raises(RemotePathError):
            _service().resolve("/home/u/proj-old/x")
        with pytest.raises(RemotePathError):
            _service().resolve("../proj-old/x")

    def test_tilde_is_refused_rather_than_passed_through(self) -> None:
        """A `~` reaching the remote unquoted expands; quoted it makes a literal `~` dir.

        Both are wrong, so it is rejected at the boundary with an error that says what to
        write instead.
        """
        with pytest.raises(RemotePathError) as excinfo:
            _service().resolve("~/elsewhere")
        assert "workspace" in str(excinfo.value)

    def test_the_root_itself_resolves_to_itself(self) -> None:
        assert _service().resolve("/home/u/proj") == "/home/u/proj"
        assert _service().resolve(".") == "/home/u/proj"


class TestPurePosixPathish:
    """The path arithmetic the boundary check is built out of.

    A remote path cannot go through `pathlib`: that answers with the *local* machine's
    separator and its idea of what is absolute, which is the wrong machine. This is the
    POSIX-only stand-in, so `resolve` is exactly as trustworthy as these two methods.
    """

    def test_normalise_collapses_dots_and_empty_segments(self) -> None:
        """Purely textual — no remote lookup happens, and none may. Asking the far end to
        resolve a path means the path has already been sent there."""
        assert str(PurePosixPathish("/a//b/./c/../d").normalise()) == "/a/b/d"

    def test_is_within_compares_segments_not_strings(self) -> None:
        root = PurePosixPathish("/a/b")
        assert PurePosixPathish("/a/b/c").normalise().is_within(root)
        assert PurePosixPathish("/a/b").normalise().is_within(root)
        assert not PurePosixPathish("/a/bc").normalise().is_within(root)
        assert not PurePosixPathish("/a").normalise().is_within(root)


# --------------------------------------------------------------------- transport args
class TestConnectionArguments:
    """What ends up on the `ssh` command line, since every command rides on it.

    Each option is one argument that is either there or not, and a wrong one fails as
    though the remote host were at fault: a missing port is "connection refused", a
    dropped identity file is "permission denied", a shared control socket is one
    session's command arriving on another session's connection.
    """

    def test_defaults_carry_no_port_key_or_jump(self) -> None:
        """A plain connection carries the multiplexing socket and nothing else. An option
        emitted with an empty value is not harmless — ssh either rejects the line or
        applies it, and "-i ''" is a key that does not exist."""
        args = _service()._base_args()
        assert args[0] == "ssh"
        assert "ControlPath=" in args[2]
        assert "-p" not in args and "-i" not in args and "-J" not in args

    def test_each_option_appears_only_when_configured(self) -> None:
        """The identity path is expanded here because ssh does not expand it: `~/.ssh/k`
        handed over literally is a key that is not found, reported as an authentication
        failure against a host that is perfectly reachable."""
        args = _service(port=2222, identity_file="~/.ssh/k", jump_host="bastion")._base_args()
        assert args[args.index("-p") + 1] == "2222"
        assert args[args.index("-i") + 1] == os.path.expanduser("~/.ssh/k")
        assert args[args.index("-J") + 1] == "bastion"

    def test_host_key_checking_is_on_unless_explicitly_disabled(self) -> None:
        """Accepting an unknown host key silently is the attack this connection invites."""
        assert "StrictHostKeyChecking=no" not in " ".join(_service()._base_args())
        relaxed = " ".join(_service(known_hosts_strict=False)._base_args())
        assert "StrictHostKeyChecking=no" in relaxed
        assert "UserKnownHostsFile=/dev/null" in relaxed

    def test_each_session_gets_its_own_control_socket(self) -> None:
        """Two sessions must not share a channel — ending one must not disturb the other."""
        config = SSHConfig(host="example", user="u")
        assert SSHService(config, "a")._socket_path() != SSHService(config, "b")._socket_path()

    def test_the_socket_path_stays_inside_the_unix_limit(self) -> None:
        """A unix socket path over ~104 bytes fails to bind, and ssh reports it obscurely."""
        service = SSHService(SSHConfig(host="a" * 200, user="u" * 200), "k" * 200)
        assert len(str(service._socket_path())) < 100


class TestSharedShellConcurrency:
    """One shell is one serial stream, and an agent batches actions."""

    @pytest.mark.asyncio
    async def test_concurrent_commands_take_turns_on_the_shared_shell(self) -> None:
        """Two coroutines reading the same stdout is an outright asyncio error.

        `read() called while another coroutine is already waiting for incoming data` —
        seen live the moment an agent issued five actions in one step.
        """
        service = _service()
        overlaps = []
        active = 0

        async def _fake_locked(remote: str, timeout: float):
            nonlocal active
            active += 1
            overlaps.append(active)
            await asyncio.sleep(0.01)
            active -= 1
            return SSHResult(exit_code=0, stdout=remote)

        service._run_in_shell_locked = _fake_locked
        results = await asyncio.gather(*(service._run_in_shell(f"cmd{i}", 5.0) for i in range(5)))

        assert max(overlaps) == 1, "commands overlapped on a single serial channel"
        assert [r.stdout for r in results] == [f"cmd{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_a_caller_stuck_behind_a_slow_command_goes_around(self) -> None:
        """Queueing must not be unbounded — past the cost of a fresh channel, take one.

        Returning None is how a caller is sent down the fallback path.
        """
        import agentevolver.environment.default.ssh.service as svc

        service = _service()
        monkeypatched, svc._SHELL_WAIT_SECONDS = svc._SHELL_WAIT_SECONDS, 0.05
        try:
            await service._shell_lock.acquire()
            assert await service._run_in_shell("ls", 5.0) is None
        finally:
            service._shell_lock.release()
            svc._SHELL_WAIT_SECONDS = monkeypatched


# --------------------------------------------------------------------- actions
class _FakeService:
    """A service that records commands and replays canned results."""

    def __init__(self, root: str = "/home/u/proj") -> None:
        self._root = root
        self.commands: List[str] = []
        self.results: Dict[str, SSHResult] = {}
        self.default = SSHResult(exit_code=0, stdout="")
        self.transfers: List[tuple] = []

    @property
    def workspace_root(self) -> str:
        return self._root

    def resolve(self, path: str) -> str:
        return SSHService.resolve(self, path)  # type: ignore[arg-type]

    def remote_spec(self, path: str) -> str:
        return f"u@example:{path}"

    async def run(self, command: str, **kwargs) -> SSHResult:
        self.commands.append(command)
        for fragment, result in self.results.items():
            if fragment in command:
                return result
        return self.default

    async def run_raw(self, command: str, **kwargs) -> SSHResult:
        return await self.run(command)

    async def rsync(self, source: str, destination: str, **kwargs) -> SSHResult:
        self.transfers.append((source, destination))
        return SSHResult(exit_code=0)

    async def is_alive(self) -> bool:
        return True

    async def stop(self) -> None:
        return None


# `_resolved_root` is what `workspace_root` reads; the fake sets `_root` instead.
_FakeService._resolved_root = None  # type: ignore[attr-defined]


class _Ctx:
    def __init__(self, sid: str = "sess1234abcd") -> None:
        self.id = sid


@pytest.fixture
def env_and_service(monkeypatch):
    from agentevolver.environment.default.ssh.environment import SSHEnvironment

    env = SSHEnvironment(host="example", user="u", workspace_root="/home/u/proj")
    fake = _FakeService()

    async def _svc(ctx, host=""):
        return fake

    monkeypatch.setattr(env, "_svc", _svc)
    return env, fake


class TestActions:
    """The surface the model calls, with the transport swapped for a recorder.

    What is read is what each action *decides* before anything leaves the machine: the
    command it builds, the paths it refuses to send at all, and how it labels what came
    back. The service is a fake precisely so that a red result here means an action
    changed its mind, never that a host was down.
    """

    @pytest.mark.asyncio
    async def test_a_nonzero_exit_is_reported_not_treated_as_a_tool_failure(
        self, env_and_service
    ) -> None:
        """`grep` exits 1 when it finds nothing. That is an answer, not a broken tool.

        Same contract as the local shell tool: the action succeeded in running the
        command, and the exit code is part of what it observed.
        """
        env, fake = env_and_service
        fake.default = SSHResult(exit_code=1, stdout="", stderr="")
        result = await env.run(command="grep nope f.txt", ctx=_Ctx())
        assert result["success"] is True
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_a_timeout_says_the_remote_process_may_still_be_running(
        self, env_and_service
    ) -> None:
        """Giving up waiting is not stopping it — the agent has to be told the difference."""
        env, fake = env_and_service
        fake.default = SSHResult(exit_code=124, timed_out=True, stderr="timed out after 60s")
        result = await env.run(command="sleep 999", ctx=_Ctx())
        assert result["timed_out"] is True

    @pytest.mark.asyncio
    async def test_write_quotes_the_path_it_was_given(self, env_and_service) -> None:
        """A remote command is text handed to a shell, so a path with a space in it is
        two arguments unless something quotes it. Unquoted, this write does not fail — it
        creates `/home/u/proj/a` and writes the content somewhere the agent will not look
        for it."""
        env, fake = env_and_service
        await env.write(path="a file.txt", content="hello", ctx=_Ctx())
        assert any(shlex.quote("/home/u/proj/a file.txt") in c for c in fake.commands)

    @pytest.mark.asyncio
    async def test_actions_refuse_a_path_outside_the_workspace(self, env_and_service) -> None:
        env, fake = env_and_service
        for call in (
            env.read(path="../../etc/passwd", ctx=_Ctx()),
            env.write(path="/etc/cron.d/x", content="x", ctx=_Ctx()),
            env.remove(path="../sibling", ctx=_Ctx()),
            env.list(path="/", ctx=_Ctx()),
        ):
            result = await call
            assert result["success"] is False
            assert "outside" in result["message"] or "workspace" in result["message"]
        assert fake.commands == [], "a refused path must not reach the remote at all"

    @pytest.mark.asyncio
    async def test_edit_refuses_an_ambiguous_match(self, env_and_service) -> None:
        """Two matches means the agent does not know which one it is changing."""
        env, fake = env_and_service
        fake.default = SSHResult(exit_code=0, stdout="x\nx\n")
        result = await env.edit(path="f.py", old="x", new="y", ctx=_Ctx())
        assert result["success"] is False
        assert "2 times" in result["message"]

    @pytest.mark.asyncio
    async def test_jobs_hides_everything_this_session_did_not_start(self, env_and_service) -> None:
        """A shared login node carries the owner's own tmux sessions.

        On the machine this was built against those were `claude`, `code` and `eval`.
        They are none of the agent's business, and an unfiltered listing would make them
        both visible and killable.
        """
        env, fake = env_and_service
        ctx = _Ctx("sess1234abcd")
        fake.default = SSHResult(
            exit_code=0,
            stdout=("ae-sess1234-train\t0\nae-other999-train\t0\nclaude\t1\neval\t0\n__LOGS__\n"),
        )
        result = await env.jobs(ctx=ctx)
        listed = {job["job"] for job in result["jobs"]}
        assert listed == {"train"}
        sessions = {job.get("session") for job in result["jobs"]}
        assert sessions == {"ae-sess1234-train"}

    @pytest.mark.asyncio
    async def test_two_sessions_get_different_job_prefixes(self, env_and_service) -> None:
        """The prefix is what `jobs` and `signal` filter on, so it is also the fence
        between two conversations' background work. Sharing one would let either session
        list and kill the other's training run."""
        env, _ = env_and_service
        assert env._job_prefix(_Ctx("aaaaaaaa")) != env._job_prefix(_Ctx("bbbbbbbb"))

    @pytest.mark.asyncio
    async def test_launch_can_be_switched_off(self, monkeypatch) -> None:
        """A host where nothing should outlive the task refuses to start background work."""
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        env = SSHEnvironment(host="example", workspace_root="/home/u/proj", allow_launch=False)
        fake = _FakeService()

        async def _svc(ctx, host=""):
            return fake

        monkeypatch.setattr(env, "_svc", _svc)
        result = await env.launch(command="train.py", name="j1", ctx=_Ctx())
        assert result["success"] is False
        assert fake.commands == []

    @pytest.mark.asyncio
    async def test_download_cannot_write_outside_the_local_session_roots(
        self, env_and_service, bound_session
    ) -> None:
        """The remote side is boundary-checked; the local side has to be too.

        Otherwise `download` is the one action in the whole environment that can write
        anywhere on the machine the agent is actually running on.

        The boundary comes from the session this run is bound to, not from the context
        handed in — a context that carried its own roots could widen its own sandbox by
        writing to the dict, which is why this test used to be able to declare them.
        """
        env, fake = env_and_service

        class _Bounded:
            id = "sess1234abcd"
            extra: dict = {}

        result = await env.download(
            remote_path="report.md", local_path="/etc/cron.d/pwned", ctx=_Bounded()
        )
        assert result["success"] is False
        assert fake.transfers == [], "a denied destination must not start a transfer"

        ok = await env.download(
            remote_path="report.md",
            local_path=str(bound_session["workspace"] / "report.md"),
            ctx=_Bounded(),
        )
        assert ok["success"] is True

    @pytest.mark.asyncio
    async def test_upload_refuses_a_local_file_that_is_not_there(self, env_and_service) -> None:
        """Checked before the transfer starts, so the agent is told it named the wrong
        file rather than reading an rsync exit status and concluding the host is
        unreachable."""
        env, fake = env_and_service
        result = await env.upload(local_path="/nonexistent/file", remote_path="x", ctx=_Ctx())
        assert result["success"] is False
        assert fake.transfers == []

    @pytest.mark.asyncio
    async def test_upload_enforces_the_size_ceiling(self, env_and_service, tmp_path) -> None:
        """A directory an agent believes is small is routinely a checkpoint or a dataset,
        and pushing it saturates the link for the rest of the run. The refusal has to
        come before the first byte moves, not partway through."""
        env, fake = env_and_service
        # A ceiling of zero refuses everything: the file's exact size is beside the point.
        env._max_upload_mb = 0
        big = tmp_path / "big.bin"
        big.write_bytes(b"0" * 4096)
        result = await env.upload(local_path=str(big), remote_path="big.bin", ctx=_Ctx())
        assert result["success"] is False
        assert fake.transfers == []


class TestLiveView:
    """The terminal a human can watch, which must never be able to take the run down."""

    @pytest.mark.asyncio
    async def test_a_view_that_was_switched_off_returns_nothing(self) -> None:
        """None, not an object that fails on use: the caller decides whether to show a
        view by whether it got one."""
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        env = SSHEnvironment(host="example", live_view=False)
        assert await env.live_view(ctx=_Ctx()) is None

    @pytest.mark.asyncio
    async def test_a_broken_view_does_not_break_the_run(self, monkeypatch) -> None:
        """The view is a convenience. Losing it must not take the task down with it."""
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        env = SSHEnvironment(host="example", live_view=True)

        async def _boom(ctx):
            raise RuntimeError("no ttyd, no network, no luck")

        monkeypatch.setattr(env, "_ensure_view", _boom)
        assert await env.live_view(ctx=_Ctx()) is None


async def _call_env_action(action):
    """Drive the real manager over one stand-in action.

    Only the action is replaced. Normalization is the manager's own, which is the whole
    point — a test that normalized the dict itself would be asserting against a copy of
    the code under test.
    """
    from unittest.mock import patch

    from agentevolver.environment.server import environment_manager

    class _Manager:
        """A callable stand-in. `SimpleNamespace(__call__=...)` does not work: Python
        looks dunders up on the type, not the instance, so the attribute is ignored."""

        async def __call__(self, name, act, input, ctx, **kwargs):
            return await action(**input)

    with patch.object(
        type(environment_manager), "environment_context_manager", _Manager(), create=True
    ):
        with patch.object(
            type(environment_manager), "_announce_live_view", AsyncMock(return_value=None)
        ):
            return await environment_manager(
                name="remote_host",
                action="run",
                input={},
                ctx=None,
            )


class TestEnvironmentResults:
    """An environment action comes back as the response every other capability returns.

    It used to come back as a bare dict, and each caller worked out for itself what had
    happened. Two did, and each guessed differently. One read `result["message"]` and
    nothing else — not one of sixteen SSH actions sets `message`, so the agent saw `None`
    from everything it did and re-ran the same actions step after step. The other passed
    the dict down a chain that takes text, and the run went silent mid-task: no error, no
    next step, ten minutes until it was killed.

    The fix was not a shared renderer for the two callers, which is what was tried first.
    It was for the manager to return what it had always declared — `success` / `message` /
    `data`, the contract tool, skill and connector had all along.
    """

    @pytest.mark.asyncio
    async def test_a_structured_result_arrives_as_text_the_model_can_read(self) -> None:
        """The failure this exists for: an action that reports data, not prose.

        `_ok(payload)` produces `{"success": True, **payload}` with no `message` at all.
        Reading only `message` returns nothing, and nothing is what the agent then saw
        from a directory listing, a file read, and a job list alike.
        """

        async def action(**kwargs):
            return {"success": True, "jobs": [{"job": "train"}], "count": 1}

        response = await _call_env_action(action)

        assert response.success
        assert json.loads(response.message) == {"jobs": [{"job": "train"}], "count": 1}
        assert response.data == {"jobs": [{"job": "train"}], "count": 1}

    @pytest.mark.asyncio
    async def test_prose_wins_when_the_action_wrote_some(self) -> None:
        """An action that says something in words should not have it replaced by JSON."""

        async def action(**kwargs):
            return {"success": True, "message": "Navigated to example.com", "url": "..."}

        response = await _call_env_action(action)

        assert response.message == "Navigated to example.com"

    @pytest.mark.asyncio
    async def test_a_failure_is_reported_as_one(self) -> None:
        """`success=False` has to survive the boundary, or the loop treats a refusal as
        an observation and carries on as though the action worked."""

        async def action(**kwargs):
            return {"success": False, "message": "path outside the workspace"}

        response = await _call_env_action(action)

        assert not response.success
        assert "outside" in response.message

    @pytest.mark.asyncio
    async def test_an_action_with_nothing_to_say_still_says_so(self) -> None:
        """Empty is a real outcome — a `remove` that removed nothing.

        Returning an empty message would put a blank observation in front of the model,
        which reads as a broken tool rather than as an action that had no output.
        """

        async def action(**kwargs):
            return {"success": True}

        assert (await _call_env_action(action)).message == "(no output)"

    @pytest.mark.asyncio
    async def test_a_response_from_an_action_is_passed_through(self) -> None:
        """The browser environment already builds `Response` itself; wrapping it again
        would nest a response inside a response and lose `data`."""
        from agentevolver.response.types import Response, ResponseType

        original = Response(
            type=ResponseType.ENVIRONMENT, success=True, message="already normalized", data={"a": 1}
        )

        async def action(**kwargs):
            return original

        assert await _call_env_action(action) is original

    def test_no_agent_carries_a_second_way_to_reach_an_environment(self) -> None:
        """Actions reach the model through one projection, not two.

        There were two: `environment_manager.function_callings()` for every agent, and an
        `env__*` projection a mixin added for the bound ones — the same actions under two
        names, dispatched by two branches. That duplication is what let one branch render
        its result and the other not.
        """
        from agentevolver.agent.actor.browser_agent import BrowserAgent
        from agentevolver.agent.actor.ssh_agent import SSHAgent
        from agentevolver.agent.types import Agent

        for cls in (Agent, BrowserAgent, SSHAgent):
            assert not hasattr(cls, "_native_env_tools")
            assert not hasattr(cls, "_handle_env_action")


class TestHostRegistry:
    """Which machines exist, where that list lives, and who may change it."""

    @pytest.fixture(autouse=True)
    def _isolated_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))

    def test_config_hosts_come_first_and_runtime_additions_follow(self) -> None:
        """Order is not cosmetic here: the first entry is the default, and that is the
        machine every action reaches when nobody named one. A store that sorted its
        names, or appended config hosts after runtime ones, would move the default
        without anybody editing a config file.
        """
        from agentevolver.environment.default.ssh.hosts import HostStore

        store = HostStore([{"name": "a", "host": "a.example"}, {"name": "b", "host": "b.example"}])
        store.add({"name": "c", "host": "c.example"})
        assert store.names() == ["a", "b", "c"]
        assert store.default().name == "a"

    def test_a_runtime_host_shadows_a_config_host_without_reordering(self) -> None:
        """Editing an entry must not move it — a list you have learned to read should stay put."""
        from agentevolver.environment.default.ssh.hosts import HostStore

        store = HostStore([{"name": "a", "host": "a.example"}, {"name": "b", "host": "b.example"}])
        store.add({"name": "a", "host": "a-new.example"})
        assert store.names() == ["a", "b"]
        assert store.get("a").host == "a-new.example"

    def test_runtime_hosts_survive_a_restart_and_config_hosts_are_not_duplicated(self) -> None:
        from agentevolver.environment.default.ssh.hosts import HostStore

        HostStore([{"name": "a", "host": "a.example"}]).add({"name": "c", "host": "c.example"})
        reopened = HostStore([{"name": "a", "host": "a.example"}])
        assert reopened.names() == ["a", "c"]

    def test_a_config_host_cannot_be_deleted_from_the_store(self) -> None:
        """The delete would only last until the next restart, and one that silently
        undoes itself is worse than one that is refused."""
        from agentevolver.environment.default.ssh.hosts import HostStore

        store = HostStore([{"name": "a", "host": "a.example"}])
        assert store.remove("a") is False
        assert store.removable("a") is False
        assert store.names() == ["a"]

    @pytest.mark.parametrize(
        "bad",
        [
            {"name": "x"},  # no address
            {"host": "h", "name": "a/b"},  # reaches a tmux session name and a path
            {"host": "h", "name": ".hidden"},
            {"host": "h", "name": "x", "port": "not-a-number"},
        ],
    )
    def test_a_malformed_host_is_refused(self, bad) -> None:
        from agentevolver.environment.default.ssh.hosts import HostStore

        with pytest.raises(ValueError):
            HostStore([]).add(bad)

    def test_the_store_holds_no_credential(self) -> None:
        """A key path is what ~/.ssh/config already keeps in plain text. A password is not."""
        from agentevolver.environment.default.ssh.hosts import RemoteHost

        fields = set(RemoteHost.__dataclass_fields__)
        assert not fields & {"password", "passphrase", "secret", "token"}


class TestMultiHostRouting:
    """One agent, several machines, and one choice of machine per session.

    Two conversations working on two clusters at once is the ordinary case, so the
    selection is per session and the override is per action. Getting that scope wrong
    produces no error anywhere: one session's `use_host` silently moves another
    session's next command to a different machine.
    """

    @pytest.fixture(autouse=True)
    def _isolated_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))

    @pytest.fixture
    def env(self):
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        return SSHEnvironment(
            hosts=[
                {"name": "alpha", "host": "a.example", "workspace_root": "/srv/alpha"},
                {"name": "beta", "host": "b.example", "workspace_root": "/srv/beta"},
            ],
            live_view=False,
        )

    def test_the_first_host_is_the_default_and_selection_is_per_session(self, env) -> None:
        one, two = _Ctx("session-one"), _Ctx("session-two")
        assert env.active_host(one).name == "alpha"
        env.select_host(one, "beta")
        assert env.active_host(one).name == "beta"
        assert env.active_host(two).name == "alpha", "one session's choice leaked into another"

    def test_every_action_takes_a_host(self, env) -> None:
        """The argument is the exception, not a routing decision on every call — but it
        has to exist on all of them, or some work is unreachable on a second machine."""
        import inspect

        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        exempt = {"hosts", "use_host"}  # these are *about* the host list
        for attribute in dir(SSHEnvironment):
            action = getattr(SSHEnvironment, attribute, None)
            name = getattr(action, "_action_name", None)
            if not name or name in exempt:
                continue
            assert "host" in inspect.signature(action).parameters, f"{name} cannot target a machine"

    @pytest.mark.asyncio
    async def test_naming_a_machine_that_does_not_exist_is_reported_not_raised(self, env) -> None:
        """A typo in a host name is the model's mistake to fix, so it comes back as a
        result it can read. Raising would surface as a tool failure with nothing to act
        on; the message lists the machines that do exist, which is what makes the next
        call correct rather than another guess."""
        result = await env.run(command="true", host="ghost", ctx=_Ctx())
        assert result["success"] is False
        assert "alpha" in result["message"] and "beta" in result["message"]

    @pytest.mark.asyncio
    async def test_with_no_hosts_the_error_says_how_to_add_one(self) -> None:
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        empty = SSHEnvironment(hosts=[], live_view=False)
        result = await empty.run(command="true", ctx=_Ctx())
        assert result["success"] is False
        assert "frontend" in result["message"] or "hosts" in result["message"]

    def test_the_single_host_config_form_still_works(self) -> None:
        """`--host` and every one-machine config already write the flat form."""
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        env = SSHEnvironment(
            host="solo.example", user="u", workspace_root="/srv/solo", live_view=False
        )
        assert env.host_store.names() == ["solo.example"]
        assert env.active_host(_Ctx()).workspace_root == "/srv/solo"


class TestEditingAHostTakesEffect:
    """A saved edit has to reach the connection, not just the record."""

    @pytest.fixture(autouse=True)
    def _isolated_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))

    @pytest.mark.asyncio
    async def test_close_host_drops_only_that_machines_connections(self) -> None:
        """A connection caches the address, the credentials and the resolved workspace
        root from when it opened, and `_svc` reuses it. Without dropping it, editing a
        machine changed the panel and nothing else: the agent kept working in the old
        directory and nothing said the two had diverged.
        """
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        env = SSHEnvironment(
            hosts=[
                {"name": "alpha", "host": "a.example"},
                {"name": "beta", "host": "b.example"},
            ],
            live_view=False,
        )

        class _Svc:
            def __init__(self):
                self.stopped = False

            async def stop(self):
                self.stopped = True

            async def run_raw(self, *a, **k):
                return SSHResult(exit_code=0)

        alpha_one, alpha_two, beta = _Svc(), _Svc(), _Svc()
        env._services[("s1", "alpha")] = alpha_one
        env._services[("s2", "alpha")] = alpha_two
        env._services[("s1", "beta")] = beta
        env._active["s1"] = "alpha"

        await env.close_host("alpha")

        assert alpha_one.stopped and alpha_two.stopped, "every session's connection must drop"
        assert not beta.stopped, "another machine's connection was collateral damage"
        assert list(env._services) == [("s1", "beta")]
        assert "s1" not in env._active, "the active pointer still names a dropped connection"

    def test_the_gateway_drops_the_connection_when_a_host_is_saved(self) -> None:
        import inspect

        from agentevolver.gateway.service import AgentGateway

        source = inspect.getsource(AgentGateway._command_environment_hosts_add)
        assert "close_host" in source, "an edit that does not reconnect does not take effect"


class TestConfig:
    """The shipped config, prompt and runner, read as the artefacts they are.

    Nothing above touches these, and each fails the same way: the run starts cleanly and
    misbehaves later. A config block under the wrong key is read as empty, a prompt that
    never names the local workspace leaves `download` with nowhere to aim, and a runner
    that starts its managers before binding a session gives the local half of the agent
    a workspace path that nothing ever creates.
    """

    def test_the_config_block_is_found_under_the_environment_name(self) -> None:
        """`env_names` and the config block agree — they did not have to before.

        The block used to have to be keyed by the underscored *class* name, which happens
        to equal the environment name for `BrowserEnvironment` and does not for this one.
        A config that named it after `env_names` was read as empty.
        """
        import inflection

        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        registered_name = SSHEnvironment.model_fields["name"].default
        assert registered_name == "remote_host"
        assert inflection.underscore("SSHEnvironment") != registered_name

    def test_the_shipped_config_leaves_the_host_blank_and_the_guards_on(self) -> None:
        """No default host: a remote agent must never connect somewhere nobody named."""
        from mmengine.config import Config as MMConfig

        cfg = MMConfig.fromfile("configs/ssh_agent.py")
        block = cfg.remote_host
        assert block["host"] == ""
        assert block["known_hosts_strict"] is True
        assert cfg.env_names == ["remote_host"]
        assert "bash_tool" in cfg.tool_names, "local tools stay: upload needs something to upload"

    def test_the_prompt_says_where_local_deliverables_go(self) -> None:
        """A result left on the remote host has not been delivered.

        The agent cannot infer the local workspace path — the base context supplies it,
        and the prompt has to actually use it or `download` has nowhere to aim.
        """
        from pathlib import Path

        prompt = Path("agentevolver/prompt/default/ssh_agent.html").read_text()
        assert "{{ workspace_root }}" in prompt
        assert "remote_host__download" in prompt

    def test_the_task_document_ships_with_the_runner(self) -> None:
        """The runner's default task must exist, or a bare `run_ssh_agent.py` fails."""
        from pathlib import Path

        runner = Path("examples/run_ssh_agent.py").read_text()
        assert "remote_workspace_audit.html" in runner
        assert Path("examples/tasks/remote_workspace_audit.html").is_file()

    def test_the_runner_binds_a_session_before_managers_start(self) -> None:
        """Without it `config.workspace_root` is a path nothing ever creates.

        The local half of this agent then has nowhere to stand: `bash_tool` refuses with
        "Workspace directory does not exist" and a `download` destination points at a
        missing directory — seen live, mid-task, after the remote work had all succeeded.
        """
        from pathlib import Path

        runner = Path("examples/run_ssh_agent.py").read_text()
        assert "ensure_session_sandbox" in runner
        assert "bind_session_roots" in runner
        # Before the managers, not after: they capture roots derived from `config.log_root`
        # when they are built.
        assert runner.index("bind_session_roots(config") < runner.index(
            "version_manager.initialize"
        )

    def test_the_environment_md_documents_every_action(self) -> None:
        """The ENVIRONMENT.md is what the agent is told the host can do."""
        from pathlib import Path

        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        md = Path("agentevolver/environment/default/ssh/ENVIRONMENT.md").read_text()
        actions = [
            getattr(getattr(SSHEnvironment, name), "_action_name")
            for name in dir(SSHEnvironment)
            if hasattr(getattr(SSHEnvironment, name, None), "_action_name")
        ]
        assert actions, "the environment registered no actions at all"
        missing = [a for a in actions if f"### {a}" not in md]
        assert not missing, f"undocumented actions: {missing}"


# --------------------------------------------------------------------- live host
_LIVE_HOST = os.environ.get("AE_SSH_TEST_HOST", "")

pytestmark_live = pytest.mark.skipif(
    not _LIVE_HOST,
    reason="set AE_SSH_TEST_HOST (and optionally AE_SSH_TEST_USER / AE_SSH_TEST_ROOT) to run",
)


@pytestmark_live
class TestAgainstARealHost:
    """The claims that only a real connection can settle."""

    @pytest_asyncio.fixture
    async def env(self):
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        environment = SSHEnvironment(
            host=_LIVE_HOST,
            user=os.environ.get("AE_SSH_TEST_USER", ""),
            identity_file=os.environ.get("AE_SSH_TEST_KEY", ""),
            workspace_root=os.environ.get("AE_SSH_TEST_ROOT", "~"),
        )
        yield environment
        await environment.cleanup()

    @pytest.mark.asyncio
    async def test_output_comes_back_exactly(self, env) -> None:
        """The sentinel that delimits results must not add or eat a newline."""
        result = await env.run(command="printf 'a\\nb'", ctx=_Ctx("livetest"))
        assert result["success"] is True
        assert result["stdout"] == "a\nb"

    @pytest.mark.asyncio
    async def test_a_write_is_readable_back(self, env) -> None:
        """Write and read are separate command shapes with separate quoting, so each can
        be right about a path the other is wrong about. Only a real filesystem settles
        whether they agree."""
        ctx = _Ctx("livetest")
        await env.write(path=".ae-test/roundtrip.txt", content="hello\n", ctx=ctx)
        read = await env.read(path=".ae-test/roundtrip.txt", ctx=ctx)
        assert "hello" in read["content"]
        await env.remove(path=".ae-test", recursive=True, ctx=ctx)

    @pytest.mark.asyncio
    async def test_a_launched_job_outlives_the_command_that_started_it(self, env) -> None:
        """The point of `launch`: the work has to survive the connection that started it,
        which no offline test can tell you. A job that dies with its ssh channel looks
        identical here to one that started and finished — until a training run vanishes
        the moment the agent moves on."""
        ctx = _Ctx("livetest")
        await env.launch(command="sleep 30", name="livejob", ctx=ctx)
        listed = await env.jobs(ctx=ctx)
        assert "livejob" in {job["job"] for job in listed["jobs"]}
        await env.signal(job="livejob", ctx=ctx)


class TestEnvironmentInstruction:
    """Environment context reaches every agent, from the file written for the model.

    It used to reach two. A mixin assembled the text by walking `info.actions`, so only
    the agents that inherited it had environment context at all — an agent with
    environments in its own `env_names` and no mixin was told nothing about them.

    The manager owns the text now, like `tool_manager.get_instruction` always has, and the
    source is always the environment's `ENVIRONMENT.md`: the file where its rules and its
    actions' arguments are written in one place a human edits and reviews. Walking
    `actions` produced a second, thinner description that had to be kept in step with it —
    and printed action names (`run`) the model cannot call, rather than the schema names
    (`remote_host__run`) it can.

    Which of the file a caller gets depends on the level, the way it does for a skill, a
    connector and a plugin: `brief` names the file, `full` is the file. Neither is a
    summary rebuilt from the registry, which is the property these tests hold.
    """

    @pytest.mark.asyncio
    async def test_the_instruction_is_the_environment_md_body(self) -> None:
        """Not a summary rebuilt from the registry. The file is the source.

        Checked at both levels, because the failure this guards against would look
        different at each: at `full` a rebuilt summary would be missing the file's own
        headings, and at `brief` it would be a second description of the environment
        instead of a pointer at the first one.
        """
        from agentevolver.environment import environment_manager

        await environment_manager.initialize(env_names=["remote_host"])

        # Asserted on the prose rather than on `##`: a card downgrades the headings
        # inside it to bold labels, so a marker-based check would be testing the
        # renderer instead of where the text came from.
        rule = "no argument switches a tool between them"

        full = await environment_manager.get_instruction(level="full")
        assert rule in full  # a sentence only the md has
        assert "upload" in full and "download" in full

        brief = await environment_manager.get_instruction(level="brief")
        assert "ENVIRONMENT.md" in brief  # where the rest is
        assert rule not in brief  # and not a second copy of it

    @pytest.mark.asyncio
    async def test_an_allowlist_selects_environments_the_way_every_manager_does(self) -> None:
        """`None` = all, `[]` = none, `[names]` = those.

        The same contract as tools, skills and connectors, so a caller does not have to
        remember which capability spells selection differently.
        """
        from agentevolver.environment import environment_manager

        await environment_manager.initialize(env_names=["remote_host"])

        assert await environment_manager.get_instruction(allowlist=[]) == ""
        assert await environment_manager.get_instruction(allowlist=["remote_host"])
        assert await environment_manager.get_instruction(allowlist=["no_such_env"]) == ""
