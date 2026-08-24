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
