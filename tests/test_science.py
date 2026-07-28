"""The Science workstation: what it does without a container running.

Container-dependent behaviour (a real GPU, a real Lab) is verified by hand
against the built image; what is worth pinning here is everything the manager
answers *off disk*, plus the argument construction that decides whether the
container gets GPUs at all.
"""

from __future__ import annotations

import asyncio
import json

from agentevolver.gateway.protocol import GatewayCommand
from agentevolver.gateway.service import AgentGateway
from agentevolver.paths import P, path_manager
from agentevolver.sandbox.default.science import ScienceSandbox
from agentevolver.sandbox.types import SandboxConfig
from agentevolver.science import science_manager


def test_notebooks_are_workspace_files_not_container_state() -> None:
    """They list before the workstation exists and after it is reaped.

    A notebook is a file in the project's workspace; the container is not. If
    this read went through the container, opening the Science view would show
    an empty list until a ~25GB image had finished booting.
    """

    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(GatewayCommand(id="c", method="session.create", params={}))
        session_id = created.result["session_id"]
        gateway._sessions[session_id].sandbox.materialize()  # noqa: SLF001

        assert science_manager.notebooks(session_id) == []

        created_nb = science_manager.create_notebook(session_id, "revenue analysis")
        assert created_nb.path == "notebooks/revenue analysis.ipynb"
        assert created_nb.cell_count == 0

        # No container was ever started.
        listed = await gateway.handle(GatewayCommand(
            id="n", method="science.notebooks", params={"session_id": session_id}))
        assert [item["title"] for item in listed.result["notebooks"]] == ["revenue analysis"]

        # It is a real notebook, openable by JupyterLab and by the Code view.
        workspace = path_manager.get(P.SESSION_WORKSPACE, owner="local", session_id=session_id)
        document = json.loads((workspace / created_nb.path).read_text(encoding="utf-8"))
        assert document["nbformat"] == 4 and document["cells"] == []
        assert document["metadata"]["kernelspec"]["name"] == "python3"

    asyncio.run(run())


def test_a_second_notebook_of_the_same_name_does_not_overwrite_the_first() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(GatewayCommand(id="c", method="session.create", params={}))
        session_id = created.result["session_id"]
        gateway._sessions[session_id].sandbox.materialize()  # noqa: SLF001

        first = science_manager.create_notebook(session_id, "untitled")
        second = science_manager.create_notebook(session_id, "untitled")
        assert first.path != second.path
        assert len(science_manager.notebooks(session_id)) == 2

    asyncio.run(run())


def test_a_daemon_without_gpus_gets_a_workstation_anyway() -> None:
    """Asking a daemon with no nvidia runtime makes the container fail to start,
    and a CPU workstation beats no workstation."""

    class FakeDaemon:
        def __init__(self, runtimes):
            self._runtimes = runtimes

        def info(self):
            return {"Runtimes": self._runtimes}

    with_gpu, without = FakeDaemon({"runc": {}, "nvidia": {}}), FakeDaemon({"runc": {}})
    assert ScienceSandbox(SandboxConfig())._requested_gpus(without) is None  # noqa: SLF001
    assert ScienceSandbox(SandboxConfig())._requested_gpus(with_gpu) == "all"  # noqa: SLF001
    # An explicit opt-out is honoured even where GPUs exist, and never asks.
    assert ScienceSandbox(SandboxConfig(gpus="none"))._requested_gpus(with_gpu) is None  # noqa: SLF001
    assert ScienceSandbox(SandboxConfig(gpus="device=0"))._requested_gpus(with_gpu) == "device=0"  # noqa: SLF001


def test_gpu_selection_becomes_the_right_device_request() -> None:
    """The SDK's equivalent of --gpus; the reason this sandbox bypasses opensandbox."""
    assert ScienceSandbox._device_request("all").get("Count") == -1  # noqa: SLF001
    assert ScienceSandbox._device_request("device=0,2").get("DeviceIDs") == ["0", "2"]  # noqa: SLF001
    assert ScienceSandbox._device_request("2").get("Count") == 2  # noqa: SLF001


def test_bind_mounts_are_translated_into_host_paths() -> None:
    """The daemon is the HOST's, reached over a mounted socket, so a path from
    inside our own container would have Docker silently create an empty
    directory — and the Lab would open on an empty workspace."""
    import os

    from agentevolver.sandbox.default.base import to_host_path

    original = os.environ.get("AGENTEVOLVER_HOST_ROOT")
    os.environ["AGENTEVOLVER_HOST_ROOT"] = "/mnt/raid/project"
    try:
        assert to_host_path("/AgentEvolver/output/local/sessions/a/workspace") == \
            "/mnt/raid/project/output/local/sessions/a/workspace"
    finally:
        if original is None:
            del os.environ["AGENTEVOLVER_HOST_ROOT"]
        else:
            os.environ["AGENTEVOLVER_HOST_ROOT"] = original


def test_compute_reports_nothing_when_no_workstation_is_running() -> None:
    """The panel shows "not running" rather than raising into the view."""

    async def run() -> None:
        status = await science_manager.compute("no-such-project")
        assert status.running is False and status.gpus == []

    asyncio.run(run())


def test_the_lab_is_served_under_the_projects_own_path() -> None:
    """Which is what lets the UI host it on whatever origin the browser used."""
    from agentevolver.science import base_path

    assert base_path("abc123") == "/science/abc123"
