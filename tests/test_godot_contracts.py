"""Regression cases from live GameBuilder calls, without touching its containers."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jsonschema
import pytest

from agentevolver.dynamic import dynamic_manager
from agentevolver.environment.default.godot.environment import GodotEnvironment
from agentevolver.environment.default.godot.inputs import compile_steps
from agentevolver.tool.default.adoption import AdoptionTool


def parameters(callable):
    inferred = dynamic_manager.get_parameters(callable)
    return dynamic_manager.build_function_calling("test", "contract", inferred)["function"]["parameters"]


def test_provider_input_schema_rejects_the_observed_flat_step():
    schema = parameters(GodotEnvironment.input_sequence)["properties"]["steps"]
    jsonschema.Draft202012Validator.check_schema(schema)
    good = [{"type": "key_tap", "arguments": {"key": "Enter"}},
            {"type": "wait", "arguments": {"duration_ms": 200}},
            {"type": "release_all"}]
    jsonschema.validate(good, schema)
    for bad in ([], good * 12, [{"type": "key_tap", "key": "Enter"}],
                [{"type": "wait", "arguments": {"duration_ms": "200"}}],
                [{"type": "unknown"}]):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)
    # Runtime keeps a useful recovery instruction for direct and older callers.
    with pytest.raises(ValueError, match='"arguments"'):
        compile_steps([{"type": "key_tap", "key": "Enter"}], {})


def test_provider_report_schema_describes_both_decisions_and_usage():
    schema = parameters(AdoptionTool)["properties"]["report"]
    jsonschema.Draft202012Validator.check_schema(schema)
    report = {"module": "tool", "name": "frame_luminance_audit", "version": "1.0.0",
              "verdict": "fail", "baseline": "Native image metrics",
              "cases": [{"case_id": "visible-metrics", "expected": "Visible metrics",
                         "observed": "Only a success sentence", "passed": False,
                         "evidence_ids": ["call_observed"]}]}
    jsonschema.validate(report, schema)
    for field in ("expected", "observed", "passed", "evidence_ids"):
        incomplete = deepcopy(report)
        incomplete["cases"][0].pop(field)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(incomplete, schema)
    jsonschema.validate({"module": "tool", "name": "frame_luminance_audit", "version": "1.0.1",
                         "consumer_call_id": "call_consumer", "evidence_ids": ["call_result"],
                         "outcome": "Measured a later gameplay frame"}, schema)
    # Preserve ordinary actions which do not submit any report.
    full = parameters(AdoptionTool)
    jsonschema.validate({"action": "list_active"}, full)
    jsonschema.validate({"action": "list_active", "report": None}, full)
    args = dynamic_manager.build_args_schema("adoption", dynamic_manager.get_parameters(AdoptionTool))
    assert args.model_validate({"report": report}).report == report


@pytest.mark.asyncio
async def test_script_path_contract_preserves_project_boundary(bound_session, monkeypatch):
    root = bound_session["workspace"]
    project = root / "game"
    project.mkdir()
    (project / "project.godot").write_text('config_version=5\n')
    (project / "tests").mkdir()
    script = project / "tests" / "probe.gd"
    script.write_text("extends SceneTree\n")
    outside = root / "reports" / "probe.gd"
    outside.parent.mkdir()
    outside.write_text("extends SceneTree\n")
    (project / "escape.gd").symlink_to(outside)
    env = GodotEnvironment()
    ctx = SimpleNamespace(id="path-contract")
    assert (await env.open_project(project_path="game", ctx=ctx))["success"]
    execute = AsyncMock(return_value={"success": True, "message": "Completed"})
    monkeypatch.setattr(GodotEnvironment, "_execute", execute)
    for action in (env.run_headless, env.check_script):
        for path in (str(outside), "../reports/probe.gd", "res://escape.gd"):
            result = await action(script=path, ctx=ctx)
            assert not result["success"]
            assert "selected Godot project" in result["message"]
            assert "tests/example.gd" in result["message"]
        execute.assert_not_called()
        for path in (str(script), "tests/probe.gd", "res://tests/probe.gd"):
            assert (await action(script=path, ctx=ctx))["success"]
        assert execute.await_count == 3
        execute.reset_mock()
