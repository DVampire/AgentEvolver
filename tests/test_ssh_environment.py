"""SSH environment: the path boundary, the transport's argument building, and the actions.

Everything here runs without a remote host. The parts that genuinely need one — that a
command's output comes back intact, that a launched job survives the connection — are in
the gated suite at the bottom, which runs only when ``AE_SSH_TEST_HOST`` names a machine.

The bias is deliberate. What can be tested offline is what *breaks* offline: the boundary
check that keeps the agent inside its workspace, and the quoting that decides which file a
path names. Both have already been wrong in this module once — a `~` that became a
directory literally named `~`, and a `pgrep` pattern that matched the shell carrying it.
"""

import asyncio
import json
import os
import shlex
from typing import Any, Dict, List, Optional

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
    def test_normalise_collapses_dots_and_empty_segments(self) -> None:
        assert str(PurePosixPathish("/a//b/./c/../d").normalise()) == "/a/b/d"

    def test_is_within_compares_segments_not_strings(self) -> None:
        root = PurePosixPathish("/a/b")
        assert PurePosixPathish("/a/b/c").normalise().is_within(root)
        assert PurePosixPathish("/a/b").normalise().is_within(root)
        assert not PurePosixPathish("/a/bc").normalise().is_within(root)
        assert not PurePosixPathish("/a").normalise().is_within(root)


# --------------------------------------------------------------------- transport args
class TestConnectionArguments:
    def test_defaults_carry_no_port_key_or_jump(self) -> None:
        args = _service()._base_args()
        assert args[0] == "ssh"
        assert "ControlPath=" in args[2]
        assert "-p" not in args and "-i" not in args and "-J" not in args

    def test_each_option_appears_only_when_configured(self) -> None:
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
    async def test_jobs_hides_everything_this_session_did_not_start(
        self, env_and_service
    ) -> None:
        """A shared login node carries the owner's own tmux sessions.

        On the machine this was built against those were `claude`, `code` and `eval`.
        They are none of the agent's business, and an unfiltered listing would make them
        both visible and killable.
        """
        env, fake = env_and_service
        ctx = _Ctx("sess1234abcd")
        fake.default = SSHResult(
            exit_code=0,
            stdout=(
                "ae-sess1234-train\t0\n"
                "ae-other999-train\t0\n"
                "claude\t1\n"
                "eval\t0\n"
                "__LOGS__\n"
            ),
        )
        result = await env.jobs(ctx=ctx)
        listed = {job["job"] for job in result["jobs"]}
        assert listed == {"train"}
        sessions = {job.get("session") for job in result["jobs"]}
        assert sessions == {"ae-sess1234-train"}

    @pytest.mark.asyncio
    async def test_two_sessions_get_different_job_prefixes(self, env_and_service) -> None:
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
        self, env_and_service, tmp_path
    ) -> None:
        """The remote side is boundary-checked; the local side has to be too.

        Otherwise `download` is the one action in the whole environment that can write
        anywhere on the machine the agent is actually running on.
        """
        env, fake = env_and_service

        class _Bounded:
            id = "sess1234abcd"
            extra = {
                "project_root": str(tmp_path),
                "workspace_root": str(tmp_path / "workspace"),
            }

        result = await env.download(
            remote_path="report.md", local_path="/etc/cron.d/pwned", ctx=_Bounded()
        )
        assert result["success"] is False
        assert fake.transfers == [], "a denied destination must not start a transfer"

        ok = await env.download(
            remote_path="report.md",
            local_path=str(tmp_path / "workspace" / "report.md"),
            ctx=_Bounded(),
        )
        assert ok["success"] is True

    @pytest.mark.asyncio
    async def test_upload_refuses_a_local_file_that_is_not_there(self, env_and_service) -> None:
        env, fake = env_and_service
        result = await env.upload(local_path="/nonexistent/file", remote_path="x", ctx=_Ctx())
        assert result["success"] is False
        assert fake.transfers == []

    @pytest.mark.asyncio
    async def test_upload_enforces_the_size_ceiling(self, env_and_service, tmp_path) -> None:
        env, fake = env_and_service
        env._max_upload_mb = 0
        big = tmp_path / "big.bin"
        big.write_bytes(b"0" * 4096)
        result = await env.upload(local_path=str(big), remote_path="big.bin", ctx=_Ctx())
        assert result["success"] is False
        assert fake.transfers == []


class TestLiveView:
    @pytest.mark.asyncio
    async def test_disabled_returns_no_view(self) -> None:
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


class TestEnvironmentBinding:
    """The mixin that projects env actions into tools, shared by browser and ssh."""

    def test_both_bound_agents_use_the_one_implementation(self) -> None:
        from agentevolver.agent.actor.browser_agent import BrowserAgent
        from agentevolver.agent.actor.ssh_agent import SSHAgent
        from agentevolver.agent.env_binding import EnvironmentBound

        for agent_cls in (BrowserAgent, SSHAgent):
            assert issubclass(agent_cls, EnvironmentBound)
            for method in ("_native_env_tools", "_handle_env_action", "_get_environment_context"):
                owner = next(c for c in agent_cls.__mro__ if method in c.__dict__)
                assert owner is EnvironmentBound, f"{agent_cls.__name__}.{method} was re-implemented"

    def test_only_bound_agents_advertise_env_tools(self) -> None:
        """`assemble_native_tools` decides by `hasattr`, so this attribute is the switch.

        On the base class it would hand env tools to every agent in the system.
        """
        from agentevolver.agent.actor.general_agent import GeneralAgent
        from agentevolver.agent.types import Agent

        assert not hasattr(Agent, "_native_env_tools")
        assert not hasattr(GeneralAgent, "_native_env_tools")

    @pytest.mark.asyncio
    async def test_structured_results_reach_the_model(self) -> None:
        """Not every action has prose to return, and the ones that do not still said something.

        Reading only `message` handed the agent `None` for every listing, read and job
        query this environment performs — so nothing it did appeared to have any effect,
        and it re-ran the same actions step after step.
        """
        from agentevolver.agent.env_binding import EnvironmentBound
        import agentevolver.agent.env_binding as binding

        async def _call(**kwargs):
            return {"success": True, "entries": ["a", "b"], "count": 2}

        bound = EnvironmentBound()
        bound.env_name = "remote_host"
        original = binding.environment_manager
        binding.environment_manager = _call
        try:
            result = await bound._handle_env_action("list", {}, None)
        finally:
            binding.environment_manager = original
        # Text, not a dict: everything downstream treats an action result as text.
        assert isinstance(result, str)
        assert json.loads(result) == {"entries": ["a", "b"], "count": 2}

    @pytest.mark.asyncio
    async def test_a_message_still_wins_when_the_action_wrote_one(self) -> None:
        from agentevolver.agent.env_binding import EnvironmentBound
        import agentevolver.agent.env_binding as binding

        async def _call(**kwargs):
            return {"success": True, "message": "done", "extra": 1}

        bound = EnvironmentBound()
        bound.env_name = "browser_environment"
        original = binding.environment_manager
        binding.environment_manager = _call
        try:
            assert await bound._handle_env_action("click", {}, None) == "done"
        finally:
            binding.environment_manager = original

    @pytest.mark.asyncio
    async def test_a_failed_action_raises_so_the_model_is_told(self) -> None:
        """Swallowing the failure would leave the agent building on a step that never ran."""
        from agentevolver.agent.env_binding import EnvironmentBound
        import agentevolver.agent.env_binding as binding

        async def _call(**kwargs):
            return {"success": False, "message": "path outside the workspace"}

        bound = EnvironmentBound()
        bound.env_name = "remote_host"
        original = binding.environment_manager
        binding.environment_manager = _call
        try:
            with pytest.raises(RuntimeError, match="outside"):
                await bound._handle_env_action("write", {}, None)
        finally:
            binding.environment_manager = original


class TestHostRegistry:
    """Which machines exist, where that list lives, and who may change it."""

    @pytest.fixture(autouse=True)
    def _isolated_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))

    def test_config_hosts_come_first_and_runtime_additions_follow(self) -> None:
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

    @pytest.mark.parametrize("bad", [
        {"name": "x"},                       # no address
        {"host": "h", "name": "a/b"},        # reaches a tmux session name and a path
        {"host": "h", "name": ".hidden"},
        {"host": "h", "name": "x", "port": "not-a-number"},
    ])
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
    @pytest.fixture(autouse=True)
    def _isolated_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))

    @pytest.fixture
    def env(self):
        from agentevolver.environment.default.ssh.environment import SSHEnvironment

        return SSHEnvironment(hosts=[
            {"name": "alpha", "host": "a.example", "workspace_root": "/srv/alpha"},
            {"name": "beta", "host": "b.example", "workspace_root": "/srv/beta"},
        ], live_view=False)

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

        env = SSHEnvironment(host="solo.example", user="u", workspace_root="/srv/solo",
                             live_view=False)
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

        env = SSHEnvironment(hosts=[
            {"name": "alpha", "host": "a.example"},
            {"name": "beta", "host": "b.example"},
        ], live_view=False)

        class _Svc:
            def __init__(self): self.stopped = False
            async def stop(self): self.stopped = True
            async def run_raw(self, *a, **k): return SSHResult(exit_code=0)

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


class TestBothRoutesRenderResults:
    """An env action reaches the loop by two routes; both must produce text.

    A bound agent projects `env__<action>`; `environment_manager.function_callings()`
    separately projects a namespace-qualified name for every agent. They are dispatched by
    different branches, and only one of them was rendering its result — so the same action
    worked under one name and hung the run under the other.
    """

    def test_the_two_dispatch_branches_share_one_renderer(self) -> None:
        import inspect

        from agentevolver.agent import types as agent_types

        source = inspect.getsource(agent_types.Agent._invoke_capability)
        environment_branch = source[source.index('if kind == "environment"'):source.index('if kind == "env"')]
        assert "render_action_result" in environment_branch

    def test_a_structured_result_renders_the_same_either_way(self) -> None:
        from agentevolver.agent.env_binding import render_action_result

        rendered = render_action_result({"success": True, "jobs": [{"job": "train"}], "count": 1})
        assert isinstance(rendered, str)
        assert json.loads(rendered) == {"jobs": [{"job": "train"}], "count": 1}

    def test_a_failure_raises_on_both_routes(self) -> None:
        from agentevolver.agent.env_binding import render_action_result

        with pytest.raises(RuntimeError, match="outside"):
            render_action_result({"success": False, "message": "path outside the workspace"})


class TestConfig:
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
        assert "env__download" in prompt

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
        assert runner.index("bind_session_roots(config") < runner.index("version_manager.initialize")

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
        ctx = _Ctx("livetest")
        await env.write(path=".ae-test/roundtrip.txt", content="hello\n", ctx=ctx)
        read = await env.read(path=".ae-test/roundtrip.txt", ctx=ctx)
        assert "hello" in read["content"]
        await env.remove(path=".ae-test", recursive=True, ctx=ctx)

    @pytest.mark.asyncio
    async def test_a_launched_job_outlives_the_command_that_started_it(self, env) -> None:
        ctx = _Ctx("livetest")
        await env.launch(command="sleep 30", name="livejob", ctx=ctx)
        listed = await env.jobs(ctx=ctx)
        assert "livejob" in {job["job"] for job in listed["jobs"]}
        await env.signal(job="livejob", ctx=ctx)
