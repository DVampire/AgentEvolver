"""The workbench reads only the selected project's bounded, existing documents."""

import os
import asyncio
from types import SimpleNamespace

import pytest

from agentevolver.gateway.overview import project_documents, read_document


def sandbox_at(root):
    return SimpleNamespace(plan_root=root / "plan", log_root=root / "log")


def test_empty_overview_does_not_create_project_state(tmp_path):
    result = project_documents(sandbox_at(tmp_path))
    assert not result["plan"]["index"]["exists"]
    assert result["memory"]["notes"] == []
    assert list(tmp_path.iterdir()) == []


def test_project_documents_are_scoped_and_private_notes_are_not_a_shared_library(tmp_path):
    first = sandbox_at(tmp_path / "first")
    second = sandbox_at(tmp_path / "second")
    for sandbox, text in [(first, "First project"), (second, "Second project")]:
        sandbox.plan_root.mkdir(parents=True)
        (sandbox.plan_root / "index.md").write_text(text)
    for actor in ("shared", "private_actor"):
        notes = first.log_root / "memory" / actor / "notes"
        notes.mkdir(parents=True)
        (notes / "method.md").write_text(actor)
    result = project_documents(first)
    assert result["plan"]["index"]["content"] == "First project"
    assert [note["content"] for note in result["memory"]["notes"]] == ["shared"]
    assert project_documents(second)["plan"]["index"]["content"] == "Second project"


def test_document_preview_is_bounded_without_rewriting_source(tmp_path):
    path = tmp_path / "plan.md"
    text = "计划" * 30_000
    path.write_text(text)
    result = read_document(tmp_path, "plan.md", limit=100)
    assert result["content"] == text[:100]
    assert result["truncated"] is True
    assert path.read_text() == text


@pytest.mark.parametrize("redirect_directory", [False, True])
def test_redirected_documents_do_not_expose_other_projects(tmp_path, redirect_directory):
    private = tmp_path / "private"
    private.mkdir()
    (private / "plan.md").write_text("Other project")
    public = tmp_path / "public"
    if redirect_directory:
        public.symlink_to(private, target_is_directory=True)
    else:
        public.mkdir()
        (public / "plan.md").symlink_to(private / "plan.md")
    result = read_document(public, "plan.md")
    assert not result["exists"]
    assert result["content"] == ""
    assert result["error"]


def test_a_fifo_cannot_block_the_overview(tmp_path):
    os.mkfifo(tmp_path / "plan.md")
    result = read_document(tmp_path, "plan.md")
    assert result["error"] == "Document is not a regular file"


@pytest.mark.asyncio
async def test_gateway_requires_a_known_project_and_reads_the_requested_one(tmp_path):
    from agentevolver.gateway.service import AgentGateway
    from agentevolver.gateway.types import GatewayCommand

    gateway = AgentGateway()
    for name in ("first", "second"):
        sandbox = sandbox_at(tmp_path / name)
        sandbox.plan_root.mkdir(parents=True)
        (sandbox.plan_root / "plan.md").write_text(name)
        gateway._sessions[name] = SimpleNamespace(sandbox=sandbox, context=SimpleNamespace(id=name), task_ids=[name])
    live = asyncio.Future()
    gateway._active_agent_tasks["second"] = live
    response = await gateway.handle(GatewayCommand(id="read", method="project.overview", params={"session_id": "second"}))
    assert response.ok
    assert response.result["plan"]["document"]["content"] == "second"
    assert response.result["runtime"]["active_tasks"] == 1
    unknown = await gateway.handle(GatewayCommand(id="missing", method="project.overview", params={"session_id": "missing"}))
    assert not unknown.ok
    linked = await gateway.handle(GatewayCommand(id="linked", method="project.document.read", params={"session_id": "first", "path": "plan.md"}))
    assert linked.ok and linked.result["content"] == "first"
    other = await gateway.handle(GatewayCommand(id="other", method="project.overview", params={"session_id": "first"}))
    assert other.result["runtime"]["active_tasks"] == 0
    live.set_result(None)
    for path in ("../second/plan/plan.md", str(tmp_path / "second/plan/plan.md"), "state.json"):
        escaped = await gateway.handle(GatewayCommand(id="escape", method="project.document.read", params={"session_id": "first", "path": path}))
        assert not escaped.ok
