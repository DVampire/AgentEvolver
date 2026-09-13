"""What a self-evolution run copies has to actually work.

`generate_skill` hands these files to the model as the shape a new component takes, and
whatever comes back is written into `{extension_root}` and registered. A template that
does not run is therefore not a documentation bug: every component generated from it is
born broken, and the failure surfaces as the *generated* agent failing to construct,
which reads like the model wrote something wrong.

Both agent templates were in exactly that state. Each hand-wrote an `__init__` whose
parameters all defaulted to `None` and forwarded them to the base, and pydantic rejects
`None` for a `str` field — so anything generated from either failed with five validation
errors before it ever ran a step. Nothing caught it because no test had ever imported a
template; they were treated as prose.
"""

import asyncio
import importlib.util
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

TEMPLATE_ROOT = Path(__file__).parents[1] / "agentevolver" / "skill" / "evolving"
#: Templates sit beside the type they build, named for the file they become — so the
#: sweep follows `template*.py` rather than the old `*_template.py` suffix.
TEMPLATES = sorted(TEMPLATE_ROOT.rglob("template*.py"))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"template_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared(module):
    """Classes the template itself defines, as opposed to what it imported."""
    return [
        value for name, value in vars(module).items()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and not name.startswith("_")
    ]


def test_there_are_templates_to_check():
    """Guards the guard: an empty sweep would pass every parametrised case below."""
    assert len(TEMPLATES) >= 3, f"found only {[str(p) for p in TEMPLATES]}"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_a_template_imports(path):
    _load(path)


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_a_template_constructs_with_no_arguments(path):
    """The generated file is instantiated by a manager that passes nothing of its own.

    So default construction is the contract, not a convenience. A template that needs an
    argument to exist cannot be copied, which is the one thing a template is for.
    """
    declared = _declared(_load(path))
    assert declared, f"{path.name} declares no class to copy"
    for cls in declared:
        try:
            cls()
        except Exception as error:  # noqa: BLE001 - any raise is a broken template
            pytest.fail(f"{path.name}: {cls.__name__}() raised "
                        f"{type(error).__name__}: {error}")


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_a_template_names_only_seams_that_still_exist(path):
    """A template pointing at a method the base no longer has teaches the model to
    override nothing.

    The tool-calling template told the model to inherit `_get_agent_context`,
    `_get_messages` and `_think_and_act`. All three were gone; the loop is
    `__call__ → think → act` now.
    """
    from agentevolver.agent.types import Agent

    text = path.read_text(encoding="utf-8")
    gone = [
        name for name in ("_get_agent_context", "_get_messages", "_think_and_act",
                          "_prepare_round", "_advance_once", "_dispatch_round",
                          "_run_one_bg", "_on_round_complete")
        if name in text and not hasattr(Agent, name)
    ]
    assert not gone, f"{path.name} names methods the base class no longer has: {gone}"


@pytest.mark.asyncio
async def test_environment_template_state_actions_and_effects():
    from agentevolver.environment.server import EnvironmentManagerServer
    from agentevolver.permission import EffectContract

    path = TEMPLATE_ROOT / "self_evolving_skill/references/environment/template.py"
    env = _load(path).MyEnvironment()
    await env.initialize()
    try:
        for action in env.actions.values():
            decision = EffectContract.from_annotations(action.metadata).policy_decision(
                mode=env.permission_mode, label=action.name,
            )
            assert decision.allowed and not decision.requires_approval

        write_effect = EffectContract.from_annotations(env.actions["set_value"].metadata)
        assert not write_effect.policy_decision(mode="read_only", label="set_value").allowed
        await env.set_value(key="phase", value="training")
        raw = await env.get_value(key="phase")
        result = EnvironmentManagerServer._normalize_response(env.name, "get_value", raw)
        assert result.success and result.data["data"]["value"] == "training"
        assert (await env.get_state(ctx=None))["state"]["keys"] == ["phase"]
        missing = EnvironmentManagerServer._normalize_response(
            env.name, "get_value", await env.get_value(key="missing"),
        )
        assert not missing.success and missing.message
    finally:
        await env.cleanup()
    assert (await env.get_state(ctx=None))["state"]["keys"] == []


@pytest.mark.asyncio
async def test_pure_tool_template_runs_through_manager_without_instance_exclusion(tmp_path, monkeypatch):
    from agentevolver.permission import permission_manager
    from agentevolver.registry import TOOL
    from agentevolver.runtime.invocation import CURRENT_RUNTIME, InvocationRuntime, ResourceClaim
    from agentevolver.tool.context import ToolContextManager
    from agentevolver.tool.types import ToolContext

    monkeypatch.setattr(TOOL, "_module_dict", dict(TOOL._module_dict))
    tool = _load(TEMPLATE_ROOT / "self_evolving_skill/references/tool/template.py").MyTool()
    manager = ToolContextManager(base_dir=str(tmp_path), default_timeout=2)
    monkeypatch.setattr(manager, "get_info", AsyncMock(return_value=SimpleNamespace(version="1", instance=tool)))
    runtime = InvocationRuntime()
    token = CURRENT_RUNTIME.set(runtime)
    entered, finish = asyncio.Event(), asyncio.Event()
    context = ToolContext(id="template-reader", workspace_root=str(tmp_path))
    async def hold_instance():
        entered.set()
        await finish.wait()
    held = asyncio.create_task(runtime.invoke("tool", "held", hold_instance, ctx=context,
        claims=(ResourceClaim(f"tool:{tool.name}"),)))
    await entered.wait()
    try:
        # The real pipeline verifies the example's read effects and runtime admission.
        with permission_manager.scope("read_only", workspace=str(tmp_path)):
            results = await asyncio.wait_for(asyncio.gather(*(
                manager(tool.name, {"arg_name": value}, ctx=context) for value in ("alpha", "beta"))), 1)
        assert all(result.success for result in results), [r.message for r in results]
        assert [r.data["arg_name"] for r in results] == ["alpha", "beta"]
        assert not held.done()
    finally:
        finish.set()
        await held
        await runtime.release()
        CURRENT_RUNTIME.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_connector_template_validation_and_native_admission_agree(tmp_path, monkeypatch, concurrent):
    from agentevolver.connector.context import ConnectorContextManager
    from agentevolver.response.types import Response
    from agentevolver.runtime.invocation import CURRENT_RUNTIME, InvocationRuntime

    root = TEMPLATE_ROOT / "self_evolving_skill"
    manifest = (root / "references/connector/template-manifest.md").read_text()
    if concurrent:
        manifest = manifest.replace("concurrent: false", "concurrent: true")
    directory = tmp_path / "my"
    directory.mkdir()
    (directory / "CONNECTOR.md").write_text(manifest)
    validate = runpy.run_path(str(root / "scripts/connector/validate.py"))["validate_connector"]
    valid, message = validate(directory)
    assert valid, message
    manager = ConnectorContextManager(base_dir=str(tmp_path), extension_connectors_dir=str(tmp_path / "extensions"))
    cfg = manager._parse_connector_dir(directory)
    assert cfg.metadata["concurrent"] is concurrent
    from agentevolver.runtime.invocation import allows_parallel
    assert allows_parallel("connector", cfg) is concurrent
    # A local read-only transport fixture replaces the external example server.
    cfg.action_annotations = {name: {"readOnlyHint": True} for name in cfg.actions}
    manager._connector_configs[cfg.name] = cfg
    active = peak = 0
    async def request(config, action, arguments):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.02)
            return Response(type="tool", success=True, message=arguments["query"])
        finally:
            active -= 1
    monkeypatch.setattr(manager, "_invoke_mcp", request)
    runtime = InvocationRuntime()
    token = CURRENT_RUNTIME.set(runtime)
    try:
        results = await asyncio.gather(*(manager(cfg.name, "search_items", {"query": value},
            ctx=SimpleNamespace(id="template-study", extra={"process_pid": value})) for value in ("a", "b")))
        assert all(r.success for r in results), [r.message for r in results]
        assert [r.message for r in results] == ["a", "b"]
        assert peak == (2 if concurrent else 1)
    finally:
        await runtime.release()
        CURRENT_RUNTIME.reset(token)


@pytest.mark.parametrize("replacement", [
    'concurrent: "true"', 'concurrent: 1', 'parallel_safe: "false"',
    'metadata:\n  concurrent: true', 'metadata:\n  parallel_safe: true',
])
def test_connector_validator_rejects_inoperative_concurrency_settings(tmp_path, replacement):
    root = TEMPLATE_ROOT / "self_evolving_skill"
    text = (root / "references/connector/template-manifest.md").read_text()
    # Remove the inline comment, which is not valid inside a replaced nested mapping.
    lines = text.splitlines()
    key = "concurrent:"
    text = "\n".join(replacement if line.startswith(key) else line for line in lines)
    (tmp_path / "CONNECTOR.md").write_text(text)
    validate = runpy.run_path(str(root / "scripts/connector/validate.py"))["validate_connector"]
    valid, message = validate(tmp_path)
    assert not valid and ("YAML boolean" in message or "top level" in message)
