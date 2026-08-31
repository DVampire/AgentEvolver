"""The Gateway's protocol surface: what it refuses, what it writes down, and what it never hands out.

The browser reaches the runtime through this object and nothing else, which puts
three separate obligations on it. Roots are server-chosen, so a client cannot
name the directory its session runs in. A project's identity — its title, its
conversations, their transcripts — is written to disk as it happens, because the
in-memory buffers die with the process and a restart would otherwise strand every
workspace with no way to reopen it. And no internal address leaves this process:
not the stored API key, not the opensandbox upstream, not the ephemeral VNC port
— because the browser is usually not on the machine the gateway runs on, and
everything looks correct from the machine it is.

These drive the service object directly through ``handle``, with no transport in
between; each test builds its own ``AgentGateway`` over the throwaway tree
``conftest`` sets up.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentevolver.canvas.types import FlowGraph, GraphNode
from agentevolver.gateway.service import AgentGateway
from agentevolver.gateway.types import PROTOCOL_VERSION, GatewayCommand
from agentevolver.job import job_manager
from agentevolver.model import model_manager
from agentevolver.model.types import ModelConfig
from agentevolver.runtime import runtime_manager
from agentevolver.runtime.types import AgentRef


# --------------------------------------------------------------------------- #
# The protocol itself
# --------------------------------------------------------------------------- #
def test_a_command_from_a_future_protocol_is_refused_rather_than_guessed_at() -> None:
    """Version is checked before the method is looked up, so a newer client cannot
    half-succeed.

    The alternative is worse than an error: an unknown field silently ignored, or
    a method whose meaning has changed executed under its old reading. A named
    error code lets the client say what it needs; a partially-applied command
    leaves the server in a state neither side agreed to.
    """

    async def run() -> None:
        gateway = AgentGateway()
        response = await gateway.handle(
            GatewayCommand(
                id="old",
                method="hello",
                params={},
                protocol_version=PROTOCOL_VERSION + 1,
            )
        )
        assert not response.ok
        assert response.error is not None
        assert response.error.code == "unsupported_protocol"

    asyncio.run(run())


async def _events_are_numbered_and_replayable() -> None:
    gateway = AgentGateway(event_history_size=10)
    queue = await gateway.subscribe()

    created = await gateway.handle(GatewayCommand(id="create", method="session.create", params={}))
    assert created.ok
    session_id = created.result["session_id"]
    assert isinstance(session_id, str)

    first = await queue.get()
    assert first.type == "session.created"
    assert first.seq_no == 1

    await gateway._publish("agent.progress", {"step": 1}, session_id=session_id)
    second = await queue.get()
    assert second.seq_no == 2

    replay = await gateway.handle(
        GatewayCommand(
            id="replay",
            method="session.events",
            params={"session_id": session_id, "after_seq": 1},
        )
    )
    assert replay.ok
    assert [event["type"] for event in replay.result["events"]] == ["agent.progress"]
    gateway.unsubscribe(queue)


def test_events_are_numbered_in_order_and_replay_from_where_a_client_left_off() -> None:
    """The sequence number is a client's only way to recover from a dropped socket.

    A slow subscriber is dropped from the live fan-out on purpose, so replay is
    not an edge case — it is the normal path back. It only works if numbering is
    gapless and monotonic and ``after_seq`` is exclusive: an off-by-one here
    either loses an event permanently or shows it twice, and both look like the
    agent misbehaving rather than the transport.
    """
    asyncio.run(_events_are_numbered_and_replayable())


def test_reconciliation_command_records_the_fact_before_resuming_the_task() -> None:
    """The client-facing recovery command preserves the backend's fail-closed order.

    A task may resume only after the human outcome reached durable Trace storage. If the
    queue transition happened first, a fast worker could repeat the uncertain action
    before the receipt survived another crash.
    """
    from agentevolver.task.server import TaskRecord, task_manager
    from agentevolver.task.types import Task, TaskStatus
    from agentevolver.trace import trace_manager
    from agentevolver.trace.execution_checkpoint import (
        ExecutionCheckpoint,
        UnsettledCall,
    )

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        session_id = str(created.result["session_id"])
        task_id = "recovered-task"
        conversation_id = "recovered-conversation"
        record = TaskRecord(
            task=Task(
                id=task_id,
                content="deploy",
                session_id=session_id,
                status=TaskStatus.WAITING_CONFIRMATION,
                metadata={"conversation_id": conversation_id},
            )
        )
        initial = ExecutionCheckpoint(
            session_id=conversation_id,
            task_id=task_id,
            state="needs_confirmation",
            unsettled_calls=[
                UnsettledCall(
                    call_id="call-1",
                    action_type="tool",
                    action_name="deploy_tool",
                    arguments={"target": "production"},
                )
            ],
        )
        settled = initial.model_copy(
            update={"state": "resumable", "unsettled_calls": []}
        )
        get_record = AsyncMock(return_value=record)
        emit = AsyncMock(return_value=True)
        flush = AsyncMock(return_value=True)
        resume = AsyncMock(return_value=True)

        with (
            patch.object(task_manager, "get", get_record),
            patch.object(task_manager, "resume_waiting", resume),
            patch.object(trace_manager, "emit", emit),
            patch.object(trace_manager, "flush", flush),
            patch.object(
                trace_manager,
                "execution_checkpoint",
                side_effect=[initial, settled],
            ),
        ):
            response = await gateway.handle(
                GatewayCommand(
                    id="reconcile",
                    method="execution.reconcile",
                    params={
                        "session_id": session_id,
                        "task_id": task_id,
                        "call_id": "call-1",
                        "outcome": "applied",
                    },
                )
            )

        assert response.ok
        assert response.result["resumed"] is True
        emitted = emit.await_args.args[0]
        assert emitted.metadata["reconciliation"]["outcome"] == "applied"
        assert flush.await_count == 1
        assert resume.await_count == 1

    asyncio.run(run())


def test_live_agent_threads_are_visible_only_to_their_gateway_project() -> None:
    """Conversation-scoped workers remain controllable from their owning project UI."""

    async def run() -> None:
        gateway = AgentGateway()
        first = await gateway.handle(
            GatewayCommand(id="first", method="session.create", params={})
        )
        second = await gateway.handle(
            GatewayCommand(id="second", method="session.create", params={})
        )
        first_id = str(first.result["session_id"])
        second_id = str(second.result["session_id"])
        job = job_manager.register(
            type="agent", label="worker", session_id="conversation-a",
        )
        ref = AgentRef(
            name="worker-ref",
            agent_name="worker",
            job_id=job.id,
            task="inspect the build",
            parent_session_id="conversation-a",
            root_session_id="conversation-a",
            project_id=first_id,
            session_id="worker-session",
            continuable=True,
        )
        runtime_manager._delegated[job.id] = ref
        try:
            visible = await gateway.handle(
                GatewayCommand(
                    id="visible",
                    method="agent.live.list",
                    params={"session_id": first_id},
                )
            )
            hidden = await gateway.handle(
                GatewayCommand(
                    id="hidden",
                    method="agent.live.list",
                    params={"session_id": second_id},
                )
            )
            delivered = await gateway.handle(
                GatewayCommand(
                    id="message",
                    method="agent.live.message",
                    params={
                        "session_id": first_id,
                        "job_id": job.id,
                        "message": "check the failing target",
                    },
                )
            )
            refused = await gateway.handle(
                GatewayCommand(
                    id="foreign",
                    method="agent.live.control",
                    params={
                        "session_id": second_id,
                        "job_id": job.id,
                        "action": "pause",
                    },
                )
            )

            assert [item["job_id"] for item in visible.result["agents"]] == [job.id]
            assert hidden.result["agents"] == []
            assert delivered.ok and delivered.result["success"]
            assert not refused.ok
        finally:
            runtime_manager._delegated.pop(job.id, None)
            job_manager.forget("conversation-a")

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Sessions, and the roots a client does not get to choose
# --------------------------------------------------------------------------- #
def test_a_client_may_not_choose_where_its_session_lives() -> None:
    """``project_root`` is refused outright rather than sanitised.

    Accepting it is the tempting shape — the field is right there in the request
    — and it would let a connected browser point a session, its workspace and its
    logs at any directory the server process can write. There is no sanitising
    version of that answer, so the parameter has no legitimate use and its
    presence is the error.
    """

    async def run() -> None:
        gateway = AgentGateway()
        response = await gateway.handle(
            GatewayCommand(
                id="create",
                method="session.create",
                params={"project_root": "/tmp/not-server-managed"},
            )
        )
        assert not response.ok
        assert response.error is not None
        assert response.error.code == "invalid_request"

    asyncio.run(run())


def test_a_workspace_the_server_did_not_offer_is_refused(tmp_path: Path) -> None:
    """The same rule for ``workspace``, which is subtler because it is sometimes legal.

    A gateway started with ``--workspace`` has exactly one source it accepts. This
    gateway was started without one, so *every* value is wrong — and a check
    written as "does this path exist and look reasonable" would pass it.
    """

    async def run() -> None:
        source = tmp_path / "source"
        source.mkdir()
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(
                id="create",
                method="session.create",
                params={"workspace": str(source)},
            )
        )
        assert not created.ok
        assert created.error is not None
        assert created.error.code == "invalid_request"

    asyncio.run(run())


def test_a_session_gets_its_own_workspace_and_leaves_nothing_on_disk_until_used(
    tmp_path: Path,
) -> None:
    """Opening a session must be free, and must not touch the source project.

    Clients open one the moment they connect and most never run anything, so a
    session that materialised its tree up front would litter the output directory
    with empty workspaces — indistinguishable from real projects to anything
    globbing for them. The workspace is also a copy, not the source: an agent
    given the developer's own checkout would edit it in place.
    """

    async def run() -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "project.txt").write_text("original", encoding="utf-8")
        gateway = AgentGateway(workspace_source=source)
        created = await gateway.handle(
            GatewayCommand(
                id="create",
                method="session.create",
                params={"workspace": str(source)},
            )
        )
        assert created.ok
        workspace = Path(created.result["workspace"])
        assert workspace != source
        # The session sandbox is lazily materialized (ProjectSandbox.create(materialize=False)):
        # opening a session must leave no empty workspace directory behind — it is created
        # on first real use, not up front.
        assert not workspace.exists()
        assert created.result["source_workspace"] == str(source)

        listed = await gateway.handle(GatewayCommand(id="list", method="session.list"))
        assert listed.ok
        assert listed.result["sessions"][0]["source_workspace"] == str(source)

    asyncio.run(run())


def test_a_session_reports_the_extension_staging_area_it_mounts() -> None:
    """What the client is shown here has to be what the container actually gets.

    The staging area is where an agent writes a component it wants promoted, and
    the mount list is how the UI explains that. Two orderings or two roots — one
    reported, one mounted — is a promotion that appears to have been written
    somewhere nothing reads, with no error at either end.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        assert created.result["extension_root"] == str(
            Path(created.result["project_root"]) / "extension"
        )

        stage = await gateway.handle(
            GatewayCommand(
                id="stage",
                method="extension.stage.get",
                params={"session_id": created.result["session_id"]},
            )
        )
        assert stage.ok
        assert stage.result["staging"]["valid"] is True
        assert stage.result["staging"]["components"] == []
        assert stage.result["mounts"][:2] == [
            {
                "source": str(Path(created.result["project_root"]) / "workspace"),
                "target": "/workspace",
                "mode": "rw",
            },
            {
                "source": str(Path(created.result["project_root"]) / "extension"),
                "target": "/extension",
                "mode": "rw",
            },
        ]

    asyncio.run(run())


def test_a_renamed_session_shows_its_new_name_in_the_list() -> None:
    """The rename has to reach the list, not just the reply that acknowledged it.

    Name lives on the session context while the list is built from the registry;
    a rename that updated only what it returned would look right in the tab that
    performed it and be gone on the next reload — the shape of bug nobody reports
    because it seems to have worked.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={"name": "web"})
        )
        assert created.ok
        session_id = created.result["session_id"]

        renamed = await gateway.handle(
            GatewayCommand(
                id="rename",
                method="session.rename",
                params={"session_id": session_id, "name": "Review authentication flow"},
            )
        )
        assert renamed.ok
        assert renamed.result["name"] == "Review authentication flow"

        listed = await gateway.handle(GatewayCommand(id="list", method="session.list"))
        assert listed.ok
        assert listed.result["sessions"][0]["name"] == "Review authentication flow"

    asyncio.run(run())


def test_a_project_is_named_by_what_was_first_asked_of_it() -> None:
    """And keeps that name — a later message must not rewrite the title.

    The list of past projects is only usable if the entries differ. They all
    used to read "web" or "Web session 10:23", which said nothing about which
    project was which.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="c", method="session.create", params={"name": "New project"})
        )
        session_id = created.result["session_id"]

        await gateway.handle(
            GatewayCommand(
                id="t1",
                method="task.submit",
                params={"session_id": session_id, "content": "Plot the revenue by quarter"},
            )
        )
        await gateway.handle(
            GatewayCommand(
                id="t2",
                method="task.submit",
                params={"session_id": session_id, "content": "Now do it by region"},
            )
        )

        listed = await gateway.handle(GatewayCommand(id="l", method="session.list", params={}))
        project = next(
            item for item in listed.result["sessions"] if item["session_id"] == session_id
        )
        assert project["name"] == "Plot the revenue by quarter"
        # The title survives a restart: it is in the manifest, not just in memory.
        restarted = AgentGateway()
        await restarted._restore_sessions()  # noqa: SLF001
        reopened = await restarted.handle(GatewayCommand(id="l2", method="session.list", params={}))
        assert (
            next(item for item in reopened.result["sessions"] if item["session_id"] == session_id)[
                "name"
            ]
            == "Plot the revenue by quarter"
        )

    asyncio.run(run())


def test_projects_are_listed_most_recently_worked_in_first() -> None:
    """The list is how someone gets back to what they were doing."""

    async def run() -> None:
        gateway = AgentGateway()
        ids = []
        for content in ("first project", "second project", "third project"):
            created = await gateway.handle(
                GatewayCommand(id=f"c{content}", method="session.create", params={})
            )
            session_id = created.result["session_id"]
            ids.append(session_id)
            await gateway.handle(
                GatewayCommand(
                    id=f"t{content}",
                    method="task.submit",
                    params={"session_id": session_id, "content": content},
                )
            )

        # Touch the oldest again; it should climb to the top.
        await gateway.handle(
            GatewayCommand(
                id="again",
                method="task.submit",
                params={"session_id": ids[0], "content": "back to the first"},
            )
        )

        # Asserting the whole order, not just the head: sessions are stored in
        # creation order, so "ids[0] is first" would also hold with no sorting
        # at all — the touched-oldest-climbs case is the one that discriminates.
        listed = await gateway.handle(GatewayCommand(id="l", method="session.list", params={}))
        assert [item["session_id"] for item in listed.result["sessions"]] == [
            ids[0],
            ids[2],
            ids[1],
        ]

    asyncio.run(run())


def test_an_untouched_session_is_listed_but_marked_as_having_no_work() -> None:
    """Both halves matter, and they are different questions.

    A reconnecting client asks this list whether its session still exists, so
    omitting empty ones would make it mint another. But a page refresh creates a
    session before the user has typed a word, so showing them all filled the
    sidebar with identical "New project" rows — has_work is what the display
    filters on.
    """

    async def run() -> None:
        gateway = AgentGateway()
        empty = (
            await gateway.handle(GatewayCommand(id="c1", method="session.create", params={}))
        ).result["session_id"]
        used = (
            await gateway.handle(GatewayCommand(id="c2", method="session.create", params={}))
        ).result["session_id"]
        await gateway.handle(
            GatewayCommand(
                id="t",
                method="task.submit",
                params={"session_id": used, "content": "Fit the model"},
            )
        )

        listed = await gateway.handle(GatewayCommand(id="l", method="session.list", params={}))
        by_id = {item["session_id"]: item for item in listed.result["sessions"]}
        # Both exist…
        assert set(by_id) == {empty, used}
        # …but only one is a project worth showing.
        assert by_id[empty]["has_work"] is False
        assert by_id[used]["has_work"] is True

        # And it survives a restart, where task_ids do not come back.
        restarted = AgentGateway()
        await restarted._restore_sessions()  # noqa: SLF001
        reopened = await restarted.handle(GatewayCommand(id="l2", method="session.list", params={}))
        restored = {item["session_id"]: item for item in reopened.result["sessions"]}
        assert restored[used]["has_work"] is True and restored[used]["task_ids"] == []
        # The empty one left no manifest, so there is nothing to restore.
        assert empty not in restored

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Capabilities and commands
# --------------------------------------------------------------------------- #
def test_a_capability_selection_is_kept_and_an_unknown_name_is_refused() -> None:
    """The selection is the session's allowlist, so an unchecked name is an escape.

    Names arrive from the client and are copied into ``tool_allowlist`` and its
    siblings, which the agent consults instead of the global registry. Accepting
    one that does not exist would either enable nothing under a name the UI shows
    as on, or — once a component by that name is registered later — quietly grant
    it. Checking against the live roster is the only reading that holds.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        session_id = created.result["session_id"]

        catalog = await gateway.handle(GatewayCommand(id="catalog", method="capability.list"))
        assert catalog.ok
        # capability.list returns descriptors ({type, name, source, evolving});
        # a selection is just the names.
        selection = {
            roster: [item["name"] for item in items[:1]]
            for roster, items in catalog.result.items()
            if isinstance(items, list)
        }
        updated = await gateway.handle(
            GatewayCommand(
                id="select",
                method="session.capabilities.set",
                params={"session_id": session_id, "capabilities": selection},
            )
        )
        assert updated.ok
        assert updated.result["capabilities"] == selection

        invalid = await gateway.handle(
            GatewayCommand(
                id="invalid",
                method="session.capabilities.set",
                params={
                    "session_id": session_id,
                    "capabilities": {**selection, "tools": ["not-a-real-tool"]},
                },
            )
        )
        assert not invalid.ok
        assert invalid.error is not None
        assert invalid.error.code == "invalid_request"

    asyncio.run(run())


def test_a_newly_registered_capability_reaches_sessions_that_are_already_open() -> None:
    """A tool the agent just built is useless if only the next session can see it.

    Self-evolution registers components mid-run, and a session's allowlist was
    frozen at creation — so the component that was just written would be filtered
    out of the very session that asked for it. Two events, not one: the catalog
    changing tells the UI what exists, and the per-session update is what actually
    permits it.
    """

    async def run() -> None:
        gateway = AgentGateway()
        catalog = {"agents": [], "tools": [], "skills": [], "connectors": []}

        async def available_capabilities() -> dict[str, list[str]]:
            return {kind: list(names) for kind, names in catalog.items()}

        gateway._available_capabilities = available_capabilities  # type: ignore[method-assign]
        queue = await gateway.subscribe()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        session_id = created.result["session_id"]
        await queue.get()

        catalog["tools"] = ["generated_tool"]
        await gateway._on_extension_change(
            {"action": "registered", "module": "tool", "name": "generated_tool", "version": "1.0.0"}
        )

        selection_event = await queue.get()
        catalog_event = await queue.get()
        assert selection_event.type == "session.capabilities.updated"
        assert selection_event.session_id == session_id
        assert selection_event.payload["capabilities"]["tools"] == ["generated_tool"]
        assert catalog_event.type == "capabilities.changed"
        assert catalog_event.payload["capabilities"]["tools"] == ["generated_tool"]
        gateway.unsubscribe(queue)

    asyncio.run(run())


def test_a_command_is_listed_with_readable_help_and_runs_in_a_session() -> None:
    """A command is documented for a person, then executed for real — both ends.

    ``/help`` is answered by the command layer, not by the gateway, so running one
    proves the dispatch reaches it with a usable context rather than that the
    string was echoed. The document assertions guard the other half: help is
    rendered prose with worked examples, because the alternative anyone reaches
    for is dumping the argument schema, which tells a user nothing about what to
    type.
    """

    async def run() -> None:
        gateway = AgentGateway()
        catalog = await gateway.handle(GatewayCommand(id="catalog", method="capability.list"))
        assert catalog.ok
        assert "commands" in catalog.result
        assert "environments" in catalog.result
        assert "inspect" in [item["name"] for item in catalog.result["commands"]]

        detail = await gateway.handle(
            GatewayCommand(
                id="detail", method="capability.get", params={"kind": "commands", "name": "inspect"}
            )
        )
        assert detail.ok
        assert detail.result["usage"] == "/inspect <type> <name>"
        assert detail.result["language"] == "markdown"
        assert "## Examples" in detail.result["document"]
        assert "`/inspect tool bash_tool`" in detail.result["document"]

        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        executed = await gateway.handle(
            GatewayCommand(
                id="help",
                method="command.execute",
                params={"session_id": created.result["session_id"], "raw": "/help"},
            )
        )
        assert executed.ok
        assert executed.result["success"] is True
        assert "[control]" in executed.result["message"]

    asyncio.run(run())


def test_tool_and_agent_details_are_human_readable_guides() -> None:
    """The last assertion is the point: a JSON schema is not documentation.

    This text is what a person reads before deciding whether to switch a tool on.
    The parameter schema is right there on the info object and dumping it is the
    path of least resistance, so the check is written as its absence.
    """
    guide = AgentGateway._capability_usage_document(
        "tools",
        "example_tool",
        SimpleNamespace(
            description="Perform an example action.", instruction="Run this only when needed."
        ),
    )
    assert "## What it does" in guide
    assert "## How to use it" in guide
    assert "Run this only when needed." in guide
    assert '"properties"' not in guide


# --------------------------------------------------------------------------- #
# Models — configurable from the browser, without the key ever coming back
# --------------------------------------------------------------------------- #
def test_the_model_catalog_groups_by_provider_and_carries_no_api_key() -> None:
    """The registry holds live credentials; this list is sent to every client.

    The tempting implementation dumps ``ModelConfig`` and picks fields out on the
    frontend, which ships the key to the browser and into anyone's devtools. The
    summary is built by naming what may leave instead, so a field added to the
    config is excluded by default rather than exposed by default — which is why
    the whole payload is asserted here, not just the absence of ``api_key``.
    """

    async def run() -> None:
        models = model_manager.model_context_manager.models
        previous_models = dict(models)
        try:
            models.clear()
            models["openai/demo"] = ModelConfig(
                model_name="openai/demo",
                model_id="demo-1",
                model_type="chat/completions",
                provider="openai",
                api_key="must-not-be-exposed",
                supports_functions=True,
            )
            response = await AgentGateway().handle(GatewayCommand(id="models", method="model.list"))
            assert response.ok
            assert response.result["providers"] == [
                {
                    "name": "openai",
                    "models": [
                        {
                            "name": "openai/demo",
                            "id": "demo-1",
                            "type": "chat/completions",
                            "streaming": True,
                            "functions": True,
                            "vision": False,
                        }
                    ],
                }
            ]
        finally:
            # The model registry is process-global; restore it or every later
            # test in this session runs against an emptied one.
            models.clear()
            models.update(previous_models)

    asyncio.run(run())


def test_editing_a_model_keeps_its_stored_key_without_ever_returning_it() -> None:
    """Editing merges onto the existing config, which is what makes this delicate.

    The client cannot be sent the key, so it cannot send it back — and an editor
    that re-registers only the fields it was given would therefore wipe the
    credential every time someone changed the temperature. Keeping it is not
    licence to echo it: the reply after the edit must be as free of the key as
    the reply before it.
    """

    async def run() -> None:
        models = model_manager.model_context_manager.models
        previous_models = dict(models)
        registered: list[ModelConfig] = []

        async def register_model(_, config: ModelConfig) -> None:
            registered.append(config)

        try:
            models.clear()
            models["openai/demo"] = ModelConfig(
                model_name="openai/demo",
                model_id="demo-1",
                model_type="chat/completions",
                provider="openai",
                api_key="must-not-be-exposed",
            )
            gateway = AgentGateway()
            detail = await gateway.handle(
                GatewayCommand(
                    id="model-detail", method="model.get", params={"name": "openai/demo"}
                )
            )
            assert detail.ok
            assert detail.result["has_api_key"] is True
            assert "api_key" not in detail.result["configuration"]

            # Registration is intercepted rather than performed: the assertion is
            # about what the gateway hands the model manager, and a real
            # re-registration would mutate the process-global registry.
            with patch.object(type(model_manager), "register_model", register_model):
                updated = await gateway.handle(
                    GatewayCommand(
                        id="model-configure",
                        method="model.configure",
                        params={
                            "original_name": "openai/demo",
                            "configuration": {"temperature": 0.2},
                        },
                    )
                )
            assert updated.ok
            assert registered[0].api_key == "must-not-be-exposed"
            assert registered[0].temperature == 0.2
            assert "api_key" not in updated.result["configuration"]
        finally:
            models.clear()
            models.update(previous_models)

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Files, and looking inside a workspace
# --------------------------------------------------------------------------- #
def test_an_uploaded_file_is_assembled_from_chunks_and_can_be_taken_back() -> None:
    """Three commands have to agree on one file before any of them is worth anything.

    Uploads arrive in 1 MB pieces over a socket that also carries every other
    command, so begin/chunk/complete is a small state machine, and the bytes on
    disk are the only proof it reassembled them in order and did not truncate the
    last one. Removal is the other half: an upload the client can no longer see
    but that is still on disk is a file nobody will ever delete.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        session_id = created.result["session_id"]

        content = b"<html><body>Agent task</body></html>"
        begun = await gateway.handle(
            GatewayCommand(
                id="begin",
                method="file.upload.begin",
                params={
                    "session_id": session_id,
                    "name": "task.html",
                    "size": len(content),
                    "mime_type": "text/html",
                },
            )
        )
        assert begun.ok
        file_id = begun.result["file"]["id"]
        chunk = await gateway.handle(
            GatewayCommand(
                id="chunk",
                method="file.upload.chunk",
                params={
                    "session_id": session_id,
                    "file_id": file_id,
                    "data": base64.b64encode(content).decode(),
                },
            )
        )
        assert chunk.ok
        completed = await gateway.handle(
            GatewayCommand(
                id="complete",
                method="file.upload.complete",
                params={"session_id": session_id, "file_id": file_id},
            )
        )
        assert completed.ok
        path = completed.result["file"]["path"]
        assert open(path, "rb").read() == content

        listed = await gateway.handle(
            GatewayCommand(id="list", method="file.list", params={"session_id": session_id})
        )
        assert listed.ok
        assert [item["name"] for item in listed.result["files"]] == ["task.html"]

        removed = await gateway.handle(
            GatewayCommand(
                id="remove",
                method="file.remove",
                params={"session_id": session_id, "file_id": file_id},
            )
        )
        assert removed.ok
        assert not Path(path).exists()

    asyncio.run(run())


def test_the_file_browser_reads_inside_the_session_and_nowhere_else(tmp_path: Path) -> None:
    """A relative path from a client is an arbitrary path until it has been checked.

    ``../source/.secret`` is the whole reason this is not a thin wrapper over
    ``open``: the escape has to be refused after resolution, not by scanning the
    string, and the session boundary is what separates one user's project from
    another's. The rest of the reads establish the browser is otherwise useful —
    hidden files stay hidden, nesting works, and the etag is a real digest rather
    than a timestamp, since that is what a concurrent edit is detected with.
    """

    async def run() -> None:
        source = tmp_path / "source"
        source.mkdir()
        gateway = AgentGateway(workspace_source=source)
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        session_id = created.result["session_id"]
        workspace = Path(created.result["workspace"])
        # Workspace is lazily materialized, so create the tree the way first real use would.
        (workspace / "src").mkdir(parents=True)
        (workspace / "src" / "app.py").write_text("print('sandbox')\n", encoding="utf-8")
        (workspace / ".secret").write_text("hidden", encoding="utf-8")

        tree = await gateway.handle(
            GatewayCommand(
                id="tree",
                method="workspace.tree",
                params={"session_id": session_id, "path": ""},
            )
        )
        assert tree.ok
        assert [entry["name"] for entry in tree.result["entries"]] == ["src"]

        nested = await gateway.handle(
            GatewayCommand(
                id="nested",
                method="workspace.tree",
                params={"session_id": session_id, "path": "src"},
            )
        )
        assert nested.result["entries"][0]["path"] == "src/app.py"

        opened = await gateway.handle(
            GatewayCommand(
                id="read",
                method="workspace.file.read",
                params={"session_id": session_id, "path": "src/app.py"},
            )
        )
        assert opened.ok
        assert opened.result["content"] == "print('sandbox')\n"
        assert opened.result["language"] == "python"
        assert len(opened.result["etag"]) == 64

        escaped = await gateway.handle(
            GatewayCommand(
                id="escape",
                method="workspace.file.read",
                params={"session_id": session_id, "path": "../source/.secret"},
            )
        )
        assert not escaped.ok
        assert escaped.error is not None
        assert escaped.error.code == "invalid_request"

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Conversations, and what a restart is allowed to forget
# --------------------------------------------------------------------------- #
def test_a_follow_up_stays_in_the_same_state_scope() -> None:
    """Otherwise "make it subtract instead" has no "it".

    ctx.id is the conversation, and memory is keyed by it. A client that omits
    conversation_id gets a fresh one per message — so the agent answering the
    second question cannot see the first. That is what the Chat view did.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(GatewayCommand(id="c", method="session.create", params={}))
        session_id = created.result["session_id"]

        first = await gateway.handle(
            GatewayCommand(
                id="t1",
                method="task.submit",
                params={
                    "session_id": session_id,
                    "view": "chat",
                    "content": "write an add function",
                },
            )
        )
        conversation = first.result["conversation_id"]

        second = await gateway.handle(
            GatewayCommand(
                id="t2",
                method="task.submit",
                params={
                    "session_id": session_id,
                    "view": "chat",
                    "content": "make it subtract instead",
                    "conversation_id": conversation,
                },
            )
        )
        assert second.result["conversation_id"] == conversation

        listed = await gateway.handle(
            GatewayCommand(
                id="l",
                method="conversation.list",
                params={"session_id": session_id, "view": "chat"},
            )
        )
        assert len(listed.result["conversations"]) == 1, "one thread, not one per message"
        assert listed.result["conversations"][0]["task_count"] == 2

        # Both turns are in one transcript, so a reload shows the exchange.
        events = await gateway.handle(
            GatewayCommand(
                id="e",
                method="conversation.events",
                params={"session_id": session_id, "conversation_id": conversation},
            )
        )
        assert [item["payload"]["content"] for item in events.result["events"]] == [
            "write an add function",
            "make it subtract instead",
        ]

    asyncio.run(run())


def test_two_conversations_in_one_project_stay_apart() -> None:
    """Their transcripts are separate, and so is the state keyed by ctx.id."""

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(GatewayCommand(id="c", method="session.create", params={}))
        session_id = created.result["session_id"]
        session = gateway._sessions[session_id]  # noqa: SLF001
        session.sandbox.materialize()
        gateway._write_session_manifest(session)  # noqa: SLF001

        first = gateway._resolve_conversation(session, {"view": "chat"})  # noqa: SLF001
        second = gateway._resolve_conversation(session, {"view": "science"})  # noqa: SLF001
        assert first.id != second.id

        await gateway._publish(
            "task.submitted",
            {"content": "one"},  # noqa: SLF001
            session_id=session_id,
            conversation_id=first.id,
        )
        await gateway._publish(
            "task.submitted",
            {"content": "two"},  # noqa: SLF001
            session_id=session_id,
            conversation_id=second.id,
        )

        async def transcript(cid):
            response = await gateway.handle(
                GatewayCommand(
                    id=f"e{cid}",
                    method="conversation.events",
                    params={"session_id": session_id, "conversation_id": cid},
                )
            )
            return [event["payload"]["content"] for event in response.result["events"]]

        assert await transcript(first.id) == ["one"]
        assert await transcript(second.id) == ["two"]

        # A view can list only its own.
        science = await gateway.handle(
            GatewayCommand(
                id="ls",
                method="conversation.list",
                params={"session_id": session_id, "view": "science"},
            )
        )
        assert [item["conversation_id"] for item in science.result["conversations"]] == [second.id]

    asyncio.run(run())


def test_a_restarted_gateway_reopens_the_conversation() -> None:
    """A transcript is the record of what happened; a restart must not eat it.

    The in-memory buffer is bounded and dies with the process, so the
    conversation is read back from its file on disk instead.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(GatewayCommand(id="c", method="session.create", params={}))
        assert created.ok
        session_id = created.result["session_id"]

        # The project becomes real — the point at which it starts leaving a
        # record, the same rule the manifest beside it follows.
        session = gateway._sessions[session_id]  # noqa: SLF001
        session.sandbox.materialize()
        gateway._write_session_manifest(session)  # noqa: SLF001

        conversation = gateway._resolve_conversation(session, {"view": "chat"})  # noqa: SLF001
        await gateway._publish(
            "task.submitted",
            {"content": "hi"},  # noqa: SLF001
            session_id=session_id,
            conversation_id=conversation.id,
        )

        before = await gateway.handle(
            GatewayCommand(
                id="e1",
                method="conversation.events",
                params={"session_id": session_id, "conversation_id": conversation.id},
            )
        )
        assert before.ok and before.result["events"], "the conversation recorded nothing"

        # A fresh gateway over the same tree — the restart.
        restarted = AgentGateway()
        await restarted._restore_sessions()  # noqa: SLF001
        assert session_id in restarted._sessions  # noqa: SLF001

        listed = await restarted.handle(
            GatewayCommand(id="l", method="conversation.list", params={"session_id": session_id})
        )
        assert [item["conversation_id"] for item in listed.result["conversations"]] == [
            conversation.id
        ]

        after = await restarted.handle(
            GatewayCommand(
                id="e2",
                method="conversation.events",
                params={"session_id": session_id, "conversation_id": conversation.id},
            )
        )
        assert [event["type"] for event in after.result["events"]] == [
            event["type"] for event in before.result["events"]
        ]

        # New events continue the numbering rather than colliding with replayed ones.
        highest = max(event["seq_no"] for event in after.result["events"])
        restored_session = restarted._sessions[session_id]  # noqa: SLF001
        restarted._resolve_conversation(restored_session, {"conversation_id": conversation.id})  # noqa: SLF001
        published = await restarted._publish(
            "test.marker",
            {},
            session_id=session_id,  # noqa: SLF001
            conversation_id=conversation.id,
        )
        assert published.seq_no > highest

    asyncio.run(run())


def test_an_older_project_keeps_its_transcript() -> None:
    """A project written before conversations existed must not lose its history.

    Its single events.jsonl would otherwise just stop being read — still on
    disk, with nothing able to open it.
    """

    async def run() -> None:
        from agentevolver.paths import P, path_manager

        gateway = AgentGateway()
        created = await gateway.handle(GatewayCommand(id="c", method="session.create", params={}))
        session_id = created.result["session_id"]
        session = gateway._sessions[session_id]  # noqa: SLF001
        session.sandbox.materialize()
        gateway._write_session_manifest(session)  # noqa: SLF001

        # A transcript in the old shape, beside the manifest.
        legacy = (
            path_manager.get(P.SESSION, owner=session.owner, session_id=session_id) / "events.jsonl"
        )
        legacy.write_text(
            '{"type": "task.submitted", "seq_no": 1, "payload": {"content": "old work"}}\n',
            encoding="utf-8",
        )

        restarted = AgentGateway()
        await restarted._restore_sessions()  # noqa: SLF001

        listed = await restarted.handle(
            GatewayCommand(id="l", method="conversation.list", params={"session_id": session_id})
        )
        assert len(listed.result["conversations"]) == 1
        adopted = listed.result["conversations"][0]["conversation_id"]

        events = await restarted.handle(
            GatewayCommand(
                id="e",
                method="conversation.events",
                params={"session_id": session_id, "conversation_id": adopted},
            )
        )
        assert [event["payload"]["content"] for event in events.result["events"]] == ["old work"]
        # The original is kept, renamed, so the rewrite is visible.
        assert not legacy.exists() and legacy.with_suffix(".jsonl.migrated").is_file()

    asyncio.run(run())


def test_canvas_run_history_outlives_the_runtime() -> None:
    """Reopening a flow shows what it did last, not an empty canvas."""

    async def run() -> None:
        from agentevolver.canvas.server import canvas_manager
        from agentevolver.paths import P, path_manager

        gateway = AgentGateway()
        created = await gateway.handle(GatewayCommand(id="c", method="session.create", params={}))
        session_id = created.result["session_id"]
        owner = gateway._sessions[session_id].owner  # noqa: SLF001

        flows = path_manager.get(P.SESSION_FLOWS, owner=owner, session_id=session_id)
        runs = path_manager.get(P.SESSION_RUNS, owner=owner, session_id=session_id)
        graph = canvas_manager.save_flow(
            FlowGraph(
                name="history",
                nodes=[GraphNode(id="a", type="step", step_type="tool", target="bash_tool")],
            ),
            flows,
        )
        canvas_manager.record_run(graph.id, "run-one", runs)
        canvas_manager.record_run(graph.id, "run-two", runs)

        listed = await gateway.handle(
            GatewayCommand(
                id="l",
                method="canvas.run.list",
                params={"session_id": session_id, "flow_id": graph.id},
            )
        )
        assert listed.ok
        # Newest first, and a run whose record is gone is reported, not hidden.
        assert [item["run_id"] for item in listed.result["runs"]] == ["run-two", "run-one"]
        assert {item["state"] for item in listed.result["runs"]} == {"unknown"}

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Live views: the gateway relays, the browser never sees the real endpoint
# --------------------------------------------------------------------------- #
def test_a_vnc_live_view_never_hands_the_client_the_raw_socket() -> None:
    """The client gets a same-origin path; the ephemeral target stays server-side.

    This has broken twice. First the field was renamed ``kind`` -> ``type`` and the check
    kept reading ``kind``, so it matched nothing. Then a second exit appeared: the reply
    to ``environment.open``, which published through this path but *returned* the raw
    view — and the reply is the one the panel reads. Both exits are covered below.

    Neither failure is visible from the machine the gateway runs on, where the ephemeral
    port genuinely exists. It only shows up as "Live view disconnected" in a browser
    somewhere else, which is the browser every real user has.
    """

    async def run() -> None:
        from agentevolver.environment.types import EnvironmentView

        gateway = AgentGateway()
        published: list = []

        async def capture(event_type, payload, **_) -> None:
            published.append((event_type, payload))

        gateway._publish = capture  # type: ignore[method-assign]  # noqa: SLF001

        await gateway._on_environment_view(
            EnvironmentView(  # noqa: SLF001
                env_name="browser", type="vnc", url="ws://172.17.0.5:41293/websockify"
            )
        )
        assert published[-1][1]["url"] == "/env/vnc/browser"
        assert gateway._vnc_targets["browser"] == "ws://172.17.0.5:41293/websockify"  # noqa: SLF001

        # A second VNC environment must not displace the first: one shared slot meant the
        # view opened earlier silently started pointing at the other one's endpoint.
        await gateway._on_environment_view(
            EnvironmentView(  # noqa: SLF001
                env_name="computer_environment", type="vnc", url="ws://172.17.0.9:5555/websockify"
            )
        )
        assert published[-1][1]["url"] == "/env/vnc/computer_environment"
        assert gateway._vnc_targets["browser"] == "ws://172.17.0.5:41293/websockify"  # noqa: SLF001

        # The other exit — what `environment.open` hands back, which is what the panel uses.
        returned = gateway._relayed_view(
            {  # noqa: SLF001
                "env_name": "computer_environment",
                "type": "vnc",
                "url": "ws://172.17.0.9:5555/websockify",
            }
        )
        assert returned["url"] == "/env/vnc/computer_environment"

        # An iframe view is a plain page; it is passed through untouched.
        await gateway._on_environment_view(
            EnvironmentView(  # noqa: SLF001
                env_name="ide", type="iframe", url="http://localhost:8080/"
            )
        )
        assert published[-1][1]["url"] == "http://localhost:8080/"

    asyncio.run(run())


def test_every_live_view_leaves_the_gateway_as_a_relay_path() -> None:
    """A raw endpoint reaching the browser is a view that can only say "disconnected".

    The address is an ephemeral port on the machine the gateway runs on; a browser
    elsewhere has forwarded the UI port and nothing else. Testing from the gateway host
    hides it entirely — the port really is there — which is why this shipped twice.

    There are two exits: the `environment.view` event and the reply to
    `environment.open`. For a while only the first rewrote anything, and the panel uses
    the second.
    """
    import inspect

    from agentevolver.gateway.service import AgentGateway

    for method in (AgentGateway._command_environment_open, AgentGateway._on_environment_view):
        source = inspect.getsource(method)
        assert "_relayed_view" in source, (
            f"{method.__name__} hands out a view without routing it through the relay"
        )


def test_vnc_targets_are_kept_per_environment() -> None:
    """One slot was enough while the browser was the only VNC environment. With two,
    the later one replaced the earlier one's target and whichever view was opened first
    went black."""
    import inspect

    from agentevolver.gateway.service import AgentGateway

    source = inspect.getsource(AgentGateway._relayed_view)
    assert "_vnc_targets[env_name]" in source
    assert "/env/vnc/{env_name}" in source
