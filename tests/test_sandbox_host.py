"""Host sandbox resources remain stoppable after their Python handle is lost."""

import os

import pytest

from agentevolver.sandbox.default.host import HostSandbox
from agentevolver.sandbox.types import SandboxConfig


@pytest.mark.asyncio
async def test_persisted_host_resource_identity_stops_the_exact_process_group(tmp_path):
    sandbox = HostSandbox(SandboxConfig(host_base=str(tmp_path)))
    await sandbox.start()
    try:
        launched = await sandbox.run_command("sleep 60 &")
        assert launched.success
        resource_id = sandbox.resource_id
        assert resource_id
        pid = int(resource_id.split(":", 1)[0])

        assert await HostSandbox.destroy_resource(resource_id)
        assert not os.path.exists(f"/proc/{pid}")
    finally:
        await sandbox.destroy()


@pytest.mark.asyncio
async def test_host_cleanup_refuses_a_stale_or_reused_identity():
    assert not await HostSandbox.destroy_resource("99999999:1:99999999")
    assert not await HostSandbox.destroy_resource("not-a-resource-id")
