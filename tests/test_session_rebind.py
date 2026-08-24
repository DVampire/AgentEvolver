"""A manager that keeps a session-derived directory follows the session by itself.

Four managers each held their own copy of "the current log root", and the gateway
re-pointed all four by name whenever a session changed:

    trace_manager.rebind(config.log_root)
    memory_manager.rebind(config.log_root)
    trajectory_manager.rebind(config.log_root)
    task_manager.rebind(...)

Four copies that can disagree, kept in step by remembering to edit one block. The line
that gets forgotten does not raise — it writes this session's files into the previous
session's directory, and the run looks fine.

`plan_manager` already documents the fix for this exact shape: *a state that changes
without saying so is a UI that lies, and the way to stop that recurring is to make the
transition responsible for it instead of each caller remembering.* Binding a session is
that transition, so it announces, and a manager subscribes at `initialize()`. A manager
added later follows by subscribing rather than by being added to somebody's list.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest
import pytest_asyncio

from agentevolver.paths import path_manager


@pytest_asyncio.fixture
async def trajectory():
    from agentevolver.trajectory import trajectory_manager

    with contextlib.redirect_stdout(io.StringIO()):
        await trajectory_manager.initialize()
    yield trajectory_manager
    path_manager.unbind_session()


@pytest.mark.asyncio
async def test_a_manager_follows_the_session_without_being_told(trajectory):
    """The whole point: nobody calls `rebind`, and the directory still moves."""
    path_manager.bind_session("local", "sess_alpha")
    assert "sess_alpha" in trajectory.base_dir

    path_manager.bind_session("local", "sess_beta")
    assert "sess_beta" in trajectory.base_dir
    assert "sess_alpha" not in trajectory.base_dir, (
        "the manager stayed pointed at the previous session; its files would land there"
    )


@pytest.mark.asyncio
async def test_the_directory_is_the_one_the_layout_table_names(trajectory):
    """Following is only right if it follows to the *same place* the table would say."""
    from agentevolver.paths import P

    path_manager.bind_session("local", "sess_layout")
    expected = path_manager.under(
        path_manager.session_roots()["log"], P.LOG_MODULE, module="trajectory")
    assert trajectory.base_dir == str(expected)


@pytest.mark.asyncio
async def test_subscribing_twice_notifies_once(trajectory):
    """A manager initialized more than once must not accumulate subscriptions.

    Duplicates are not merely wasteful here: each one re-derives and re-assigns the same
    directory, so a leak would be invisible until something in a listener had a side
    effect worth doing only once.
    """
    calls = []
    listener = calls.append

    path_manager.on_rebind(lambda: listener("x"))
    before = len(path_manager._listener_list())
    from agentevolver.trajectory import trajectory_manager

    with contextlib.redirect_stdout(io.StringIO()):
        await trajectory_manager.initialize()
    after = len(path_manager._listener_list())
    assert after == before, "re-initializing added a second subscription"


@pytest.mark.asyncio
async def test_a_failing_listener_does_not_stop_the_rebind(trajectory):
    """The session has already changed by the time listeners run.

    Refusing to finish because one manager raised would leave every other manager bound to
    a session the caller believes it has left — the worst of both states.
    """
    def _explode():
        raise RuntimeError("this listener is broken")

    path_manager.on_rebind(_explode)
    try:
        path_manager.bind_session("local", "sess_after_failure")
        assert path_manager.session == ("local", "sess_after_failure")
        assert "sess_after_failure" in trajectory.base_dir
    finally:
        path_manager._listener_list().remove(_explode)


def test_the_gateway_no_longer_names_managers_one_by_one():
    """The list is what this replaced, so its absence is the assertion.

    A reviewer adding a fifth manager should find no list to add it to — that is what
    makes subscribing the obvious thing to do instead.
    """
    import inspect

    from agentevolver.gateway.service import AgentGateway

    source = inspect.getsource(AgentGateway._bind_runtime_to_session)
    for named in ("trace_manager.rebind", "memory_manager.rebind",
                  "trajectory_manager.rebind", "task_manager.rebind"):
        assert named not in source, f"{named} is still called by name on session change"
    assert "path_manager.bind_session" in source


def test_every_manager_that_can_rebind_subscribes_to_the_session():
    """A `rebind` nothing calls is a directory that silently stops following.

    Checked across the managers rather than one by one: the failure this guards is a new
    manager growing a `rebind` and never being wired to anything — which looks exactly
    like the four that were wired to the gateway's list.
    """
    import inspect

    from agentevolver.memory import memory_manager
    from agentevolver.task import task_manager
    from agentevolver.trace import trace_manager
    from agentevolver.trajectory import trajectory_manager

    for manager in (memory_manager, task_manager, trace_manager, trajectory_manager):
        assert hasattr(manager, "_follow_session"), (
            f"{type(manager).__name__} has rebind but no way to hear about a session change"
        )
        source = inspect.getsource(type(manager).initialize)
        assert "on_rebind" in source, (
            f"{type(manager).__name__}.initialize does not subscribe, so its rebind is dead"
        )


def test_a_container_mount_override_reaches_the_sandbox_boundary():
    """`path_manager.override` exists for one case, and the boundary did not honour it.

    Its own docstring names that case: "a task running inside a container sees its
    workspace at the mount point, and no host-side table can derive a mount point from
    the host path." ProgramBench's inner run overrides `SESSION_WORKSPACE` to
    `/workspace` for exactly that reason.

    `session_roots()` then resolved every key with `get(key, owner=..., session_id=...)`,
    and `get` treats explicit params as "tell me about a *specific* session" — a question
    an override, which is a statement about the current run, deliberately does not
    answer. The two values passed were the bound session's own, so they added nothing and
    turned the override off.

    The cost was not abstract. `write_file_tool` on `/workspace/cmatrix.c` — the
    deliverable, at the path the task document names — was refused as "outside allowed
    roots" while a `bash` heredoc into the same directory succeeded, because bash is not
    path-checked the same way. Sixteen refusals in one instance, nine of them the source
    file being reconstructed, and the agent recorded in its own plan that `/workspace`
    must be a symlink. It is not: same inode, writable.
    """
    from agentevolver.paths import P
    from agentevolver.sandbox.project import check_session_path, session_writable_roots

    path_manager.bind_session("local", "mount_probe")
    try:
        assert check_session_path(path="/workspace/deliverable.c", write=True), (
            "without the override the mount point must still be outside the boundary")

        path_manager.override(P.SESSION_WORKSPACE, "/workspace")

        assert session_writable_roots()[0] == Path("/workspace"), (
            f"session_roots() ignored the override: {session_writable_roots()[0]}")
        assert check_session_path(path="/workspace/deliverable.c", write=True) is None, (
            "the agent still cannot write the deliverable at the path it is told to use")
        # The point is a mount alias, not a wider boundary.
        assert check_session_path(path="/etc/passwd", write=True), (
            "honouring the override opened the boundary instead of relocating it")
        assert check_session_path(path="/workspace/../etc/passwd", write=True), (
            "a traversal out of the mount point is still outside it")
    finally:
        path_manager.unbind_session()


def test_an_override_moves_what_lives_inside_it():
    """A declaration about a directory is a declaration about what is in it.

    `override` replaced one key. Everything nested under it kept resolving from the
    table, so a run that declared its workspace at a container mount point moved the
    workspace and left `workspace/plan.md` and `workspace/notebooks` at the host layout
    path — two names for one directory, and whichever a caller happened to use decided
    whether the agent could write there.

    Cascading in `get` rather than deriving at each nested key, because there is nothing
    special about those two: the next key added under an overridden one gets it without
    anyone remembering. Which is the failure this replaces — the plan was fixed by hand
    and `session_notebooks`, identical in shape, was not.
    """
    from agentevolver.paths import P

    path_manager.bind_session("local", "cascade")
    try:
        path_manager.override(P.SESSION_WORKSPACE, "/workspace")

        assert path_manager.get(P.SESSION_WORKSPACE) == Path("/workspace")
        assert path_manager.get(P.SESSION_PLAN) == Path("/workspace/plan.md")
        assert path_manager.get(P.SESSION_NOTEBOOKS) == Path("/workspace/notebooks")

        # Only what is inside. A sibling of the overridden key is a different directory.
        log = path_manager.get(P.SESSION_LOG)
        assert not str(log).startswith("/workspace"), (
            f"the cascade escaped the directory it was declared about: {log}")
    finally:
        path_manager.unbind_session()


def test_asking_about_another_session_is_not_answered_by_this_ones_override():
    """The cascade inherits the rule it cascades: explicit params mean a specific run."""
    from agentevolver.paths import P

    path_manager.bind_session("local", "cascade_other")
    try:
        path_manager.override(P.SESSION_WORKSPACE, "/workspace")
        other = path_manager.get(P.SESSION_PLAN, owner="local", session_id="someone_else")
        assert not str(other).startswith("/workspace"), (
            f"another run's plan was answered from this run's mount point: {other}")
    finally:
        path_manager.unbind_session()


def test_naming_the_bound_run_is_the_same_question_as_naming_nothing():
    """Whether a call is about the current session is `path_manager`'s rule to know.

    It used to be the caller's. `plan_path` worked it out — bound owner, bound session,
    compare — and `kernel/notebooks.py`, identical in shape, did not and so kept the
    trap. One rule in one place means a module gets the behaviour by resolving a key,
    not by remembering to reimplement the comparison.
    """
    from agentevolver.paths import P

    path_manager.bind_session("local", "same_question")
    try:
        path_manager.override(P.SESSION_WORKSPACE, "/workspace")
        bare = path_manager.get(P.SESSION_PLAN)
        named = path_manager.get(P.SESSION_PLAN, owner="local", session_id="same_question")
        assert bare == named == Path("/workspace/plan.md")
        # `notebooks` gets this without a line of its own.
        assert path_manager.get(P.SESSION_NOTEBOOKS, owner="local",
                                session_id="same_question") == Path("/workspace/notebooks")
        # A parameter the bound session does not know keeps it a different question.
        module_path = path_manager.get(P.LOG_MODULE, module="trace")
        assert not str(module_path).startswith("/workspace")
    finally:
        path_manager.unbind_session()
