"""Delegated agents edit isolated worktrees and return only their own patch.

A child that shares the parent's dirty tree can overwrite concurrent work or claim the
parent's changes as its result. The tests cover baseline capture, scoped subdirectories,
cleanup, result envelopes, and fallback behavior outside Git repositories.
"""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentevolver.agent.actor.general_agent import GeneralAgent
from agentevolver.agent.types import Agent, AgentContext, _child_result_envelope
from agentevolver.agent.worktree import IsolatedWorktree
from agentevolver.constraint import TokenConstraint
from agentevolver.response import Response, ResponseType


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def test_isolated_worktree_starts_from_parent_state_and_returns_only_child_patch(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")
    (repository / "tracked.txt").write_text("parent state\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("parent untracked\n", encoding="utf-8")

    async def scenario():
        worktree = await IsolatedWorktree.create(
            str(repository),
            str(tmp_path / "logs"),
            "call/one",
        )
        assert (worktree.path / "tracked.txt").read_text() == "parent state\n"
        assert (worktree.path / "untracked.txt").read_text() == "parent untracked\n"
        (worktree.path / "tracked.txt").write_text("child state\n", encoding="utf-8")
        patch = await worktree.collect_patch()
        location = worktree.path
        await worktree.cleanup()
        return patch, location

    patch, location = asyncio.run(scenario())
    assert "+child state" in patch
    assert "parent untracked" not in patch
    assert not location.exists()
    assert (repository / "tracked.txt").read_text() == "parent state\n"


def test_isolated_worktree_preserves_repository_subdirectory_as_child_cwd(tmp_path):
    repository = tmp_path / "monorepo"
    workspace = repository / "packages" / "app"
    workspace.mkdir(parents=True)
    _git(repository, "init")
    _git(repository, "config", "user.name", "test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (workspace / "app.py").write_text("old\n", encoding="utf-8")
    (repository / "outside.txt").write_text("outside\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")
    (workspace / "app.py").write_text("parent\n", encoding="utf-8")
    (workspace / "local.txt").write_text("local\n", encoding="utf-8")
    # A dirty file outside the scoped workspace must not leak into its baseline patch.
    (repository / "outside.txt").write_text("parent outside\n", encoding="utf-8")

    async def scenario():
        worktree = await IsolatedWorktree.create(
            str(workspace),
            str(tmp_path / "logs"),
            "subdir",
        )
        assert worktree.path.relative_to(worktree.worktree_root) == Path("packages/app")
        assert (worktree.path / "app.py").read_text() == "parent\n"
        assert (worktree.path / "local.txt").read_text() == "local\n"
        assert (worktree.worktree_root / "outside.txt").read_text() == "outside\n"
        (worktree.path / "app.py").write_text("child\n", encoding="utf-8")
        patch = await worktree.collect_patch()
        root = worktree.worktree_root
        await worktree.cleanup()
        return patch, root

    patch, root = asyncio.run(scenario())
    assert "+child" in patch
    assert "outside.txt" not in patch
    assert not root.exists()


def test_child_result_envelope_is_structured_and_keeps_patch_and_evidence():
    response = Response(
        type=ResponseType.AGENT,
        success=False,
        message="implemented most of it",
        files=["src/a.py"],
        data={
            "step": 5,
            "max_step": 5,
            "tests": ["pytest tests/test_a.py"],
            "blockers": ["missing fixture"],
            "remaining": ["add fixture"],
            "evidence": ["test output"],
        },
    )
    envelope = _child_result_envelope(
        response,
        patch="diff --git a/src/a.py b/src/a.py",
        read_set=["src"],
        write_set=["src/a.py"],
        acceptance=["tests pass"],
    )
    assert '"status": "partial"' in envelope
    assert '"patch": "diff --git' in envelope
    assert '"tests": ["pytest tests/test_a.py"]' in envelope
    assert '"blockers": ["missing fixture"]' in envelope


def test_child_result_envelope_treats_scalar_metadata_as_one_item():
    response = Response(
        type=ResponseType.AGENT,
        success=True,
        message="done",
        data={"tests": "pytest -q", "evidence": "42 passed"},
    )

    envelope = _child_result_envelope(response)

    assert '"tests": ["pytest -q"]' in envelope
    assert '"evidence": ["42 passed"]' in envelope


def test_dispatch_schema_exposes_complete_task_resource_contract():
    properties = (
        Agent._dispatch_parameters()["properties"]
        if hasattr(Agent, "_dispatch_parameters")
        else None
    )
    # The schema is owned by AgentManager rather than the Agent runtime.
    if properties is None:
        from agentevolver.agent.server import AgentManagerServer

        properties = AgentManagerServer._dispatch_parameters()["properties"]

    assert {
        "read_set",
        "write_set",
        "owner",
        "model",
        "reasoning_effort",
        "token_budget",
        "acceptance",
        "isolate_worktree",
    }.issubset(properties)


@pytest.mark.asyncio
async def test_child_model_and_budget_overrides_are_invocation_local(monkeypatch, tmp_path):
    parent = GeneralAgent(base_dir=str(tmp_path), model_name="parent-model", max_step=2)
    registered = GeneralAgent(base_dir=str(tmp_path), model_name="default-model", max_step=2)
    captured = {}

    async def get_child(_name):
        return registered

    async def get_constraint(_name):
        return TokenConstraint()

    async def delegate(child, task, **brief):
        captured.update(child=child, task=task, brief=brief)
        return Response(type=ResponseType.AGENT, success=True, message="done")

    monkeypatch.setattr("agentevolver.agent.agent_manager.get", get_child)
    monkeypatch.setattr("agentevolver.constraint.constraint_manager.get", get_constraint)
    monkeypatch.setattr("agentevolver.runtime.runtime_manager.delegate", delegate)
    call = SimpleNamespace(
        id="call-1",
        input={
            "task": "implement",
            "owner": "planner",
            "model": "special-model",
            "reasoning_effort": "medium",
            "token_budget": 1234,
            "read_set": ["src"],
            "write_set": [],
        },
    )

    output, *_ = await parent._invoke_capability(
        ("agent", "worker"),
        call,
        AgentContext(id="session"),
    )

    child = captured["child"]
    child_ctx = captured["brief"]["parent_ctx"]
    assert child is not registered
    assert child.model_name == "special-model" and child.max_token == 1234
    assert registered.model_name == "default-model" and registered.max_token is None
    assert child_ctx.extra["child_reasoning_effort"] == "medium"
    assert child_ctx.extra["task_contract"]["owner"] == "planner"
    assert child_ctx.extra["task_contract"]["read_set"] == ["src"]
    assert child_ctx.extra["task_contract"]["write_set"] == []
    inherited = await child._get_inherited_context(child_ctx)
    assert "### Delegation contract" in inherited["inherited_context"]
    assert '"read_set": [' in inherited["inherited_context"]
    assert '"token_budget": 1234' in output
