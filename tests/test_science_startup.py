"""A failed kernel startup must reach the Science user as an error."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentevolver.gateway.service import AgentGateway
from agentevolver.gateway.types import GatewayCommand
from agentevolver.kernel import KernelResult, kernel_manager
from agentevolver.kernel import server as kernel_server


@pytest.mark.asyncio
async def test_science_start_does_not_report_a_failed_kernel_as_ready(monkeypatch, tmp_path):
    gateway = AgentGateway()
    gateway._sessions['sample'] = SimpleNamespace(
        owner='local', sandbox=SimpleNamespace(workspace_root=tmp_path),
    )
    monkeypatch.setattr(kernel_manager, 'execute', AsyncMock(return_value=KernelResult(
        success=False, error='JupyterLab is missing',
    )))
    monkeypatch.setattr('agentevolver.gateway.service.kernel_notebooks.directory', lambda *a, **k: tmp_path)
    response = await gateway.handle(GatewayCommand(
        id='start', method='science.start', params={'session_id':'sample'},
    ))
    assert not response.ok
    assert 'JupyterLab is missing' in response.error.message
    assert 'path' not in response.result


@pytest.mark.asyncio
async def test_missing_jupyterlab_fails_before_spawning(monkeypatch, tmp_path):
    manager = kernel_server.KernelManagerServer()
    spawn = Mock()
    monkeypatch.setattr(kernel_server.importlib.util, 'find_spec', lambda _: None)
    monkeypatch.setattr(kernel_server.subprocess, 'Popen', spawn)
    with pytest.raises(RuntimeError, match='science extra'):
        await manager._ensure_server('sample', str(tmp_path))
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_jupyter_uses_the_gateway_interpreter(monkeypatch, tmp_path):
    manager = kernel_server.KernelManagerServer()
    spawn = Mock(return_value=Mock())
    monkeypatch.setattr(kernel_server.importlib.util, 'find_spec', lambda _: object())
    monkeypatch.setattr(kernel_server.subprocess, 'Popen', spawn)
    monkeypatch.setattr(manager, '_wait_ready', AsyncMock(return_value=True))
    monkeypatch.setattr(manager, '_ensure_reaper', lambda: None)
    await manager._ensure_server('sample', str(tmp_path))
    assert spawn.call_args.args[0][:3] == [sys.executable, '-m', 'jupyterlab']
