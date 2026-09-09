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


def test_responses_input_contract_does_not_force_unrelated_argument_fields():
    from agentevolver.model.llm_hub.response import serialize_tools

    function = dynamic_manager.build_function_calling(
        "godot_environment__input_sequence", "Player input",
        dynamic_manager.get_parameters(GodotEnvironment.input_sequence),
    )
    tool = SimpleNamespace(function_calling=function, metadata={})
    original = deepcopy(function)
    wire = serialize_tools([tool])[0]
    assert wire["strict"] is False
    assert function == original
    schema = wire["parameters"]
    valid = {"steps": [{"type": "key_tap", "arguments": {"key": "Enter"}},
                       {"type": "key_down", "arguments": {"key": "W"}},
                       {"type": "wait", "arguments": {"duration_ms": 850}},
                       {"type": "key_up", "arguments": {"key": "W"}}]}
    jsonschema.validate(valid, schema)
    for extra in ({"x": 0, "y": 0, "type": "button"}, {"duration_ms": 0}, {"action": ""}):
        padded = deepcopy(valid)
        padded["steps"][0]["arguments"].update(extra)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(padded, schema)


@pytest.mark.parametrize("kind,args", [
    ("key_down", {"action": "move_forward"}), ("key_up", {"key": "W"}),
    ("key_tap", {"key": "S", "ctrl": True}), ("text", {"text": "潮汐"}),
    ("click", {"x": 0, "y": 0}), ("double_click", {"x": 1, "y": 2}),
    ("mouse_down", {"x": 1, "y": 2, "button": 1}), ("mouse_up", {"x": 1, "y": 2}),
    ("mouse_move", {"x": 0, "y": 0, "relative_x": -10, "relative_y": 5}),
    ("drag", {"fromX": 1, "fromY": 2, "toX": 3, "toY": 4}),
    ("scroll", {"x": 1, "y": 2, "direction": "down", "amount": 2}),
    ("gamepad", {"type": "axis", "index": 0, "value": -0.5}),
    ("touch", {"action": "press", "x": 1, "y": 2}),
    ("action_strength", {"actionName": "move_forward", "strength": 0}),
    ("mouse_mode", {"mode": "visible"}), ("wait", {"duration_ms": 0}),
    ("wait_frames", {"frames": 2, "frameType": "physics"}), ("release_all", {}),
])
def test_operation_specific_schema_preserves_supported_player_inputs(kind, args):
    schema = parameters(GodotEnvironment.input_sequence)["properties"]["steps"]
    jsonschema.validate([{"type": kind, "arguments": args}], schema)


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


def test_headless_log_preserves_original_error_before_shutdown_noise(tmp_path):
    log = tmp_path / "engine.log"
    first = 'SCRIPT ERROR: Invalid access to property data\n    at: run (res://tests/core.gd:23)\n'
    log.write_text(first + "shutdown resource detail\n" * 3000 + "finished\n")
    shown, error = GodotEnvironment._read_log(log)
    assert error
    assert first in shown
    assert shown.endswith("finished\n")
    assert len(shown) < 12000


@pytest.mark.asyncio
async def test_export_smoke_validates_artifact_before_starting_engine(bound_session, monkeypatch):
    root = bound_session["workspace"]
    project = root / "game"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n")
    build = root / "builds"
    build.mkdir()
    pck = build / "game.pck"
    pck.write_bytes(b"GDPCdata")
    pck.chmod(0o755)
    nonexec = build / "not-executable"
    nonexec.write_bytes(b"\x7fELF")
    escaped = build / "escape"
    escaped.symlink_to("/bin/true")
    ctx = SimpleNamespace(id="export-contract")
    env = GodotEnvironment()
    assert (await env.open_project(project_path="game", ctx=ctx))["success"]
    execute = AsyncMock()
    monkeypatch.setattr(GodotEnvironment, "_execute", execute)
    for path in ("builds/missing", "builds/game.pck", "builds/not-executable", "builds/escape",
                 "game/project.godot", "/bin/true", ""):
        result = await env.run_export(executable_path=path, ctx=ctx)
        assert not result["success"], path
    execute.assert_not_called()
    schema = parameters(GodotEnvironment.run_export)
    jsonschema.validate({"executable_path": "builds/r1/game.x86_64", "frames": 10, "timeout": 20}, schema)


def test_headless_pass_marker_does_not_hide_exit_resource_error(tmp_path):
    log = tmp_path / "engine.log"
    body = "PASS battle return\nERROR: 1 resources still in use at exit\n"
    log.write_text(body)
    shown, error = GodotEnvironment._read_log(log)
    assert error and shown == body
    log.write_text("PASS battle return\n")
    assert GodotEnvironment._read_log(log) == ("PASS battle return\n", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_lifecycle_archives_full_log_and_bounds_repeated_process_output(tmp_path, monkeypatch, ok):
    from pathlib import Path
    from mcp.types import CallToolResult, TextContent

    text = ('{"success":' + str(ok).lower() + ', "output": [\n'
            + '"heartbeat request_completed",\n' * 300
            + '"SCRIPT ERROR: invalid property at core.gd:23",\n'
            + '"heartbeat request_completed",\n' * 300 + '"finished"]}')
    raw = CallToolResult(content=[TextContent(type="text", text=text)], isError=not ok)
    runtime = SimpleNamespace(call=AsyncMock(return_value=raw))
    env = GodotEnvironment()
    monkeypatch.setattr(GodotEnvironment, "_runtime", lambda *args: runtime)
    rec = {"workspace": tmp_path}
    result = await env._mcp("test", rec, "stop_project", {})
    assert result["success"] == ok
    assert "invalid property at core.gd:23" in result["message"]
    assert len(result["message"]) < 4500
    archive = Path(result["extra"]["log_path"])
    assert archive.read_text() == text
    assert str(archive) in result["message"]
    assert rec["last"]["output"] == result["message"]
