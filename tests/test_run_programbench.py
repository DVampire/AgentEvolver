import argparse
import os
import sys
from pathlib import Path

import pytest

root = str(Path(__file__).resolve().parents[1])
sys.path.append(root)

from agentevolver.config import config


def test_programbench_agent_config_loads_expected_base_roster():
    config.initialize(
        config_path=os.path.join(root, "configs", "programbench_agent.py"),
        args=argparse.Namespace(),
        verbose=False,
    )
    assert "meta_agent" in config.agent_names
    assert "code_agent" in config.agent_names
    assert "general_agent" in config.agent_names
    assert "reviewer_agent" in config.agent_names
    assert "monitor_agent" in config.agent_names
    # Base roster excludes the self-evolution add-ons — the running script adds
    # them at runtime via extend_roster_for_evolve() when --evolve is set.
    assert "tool_optimize_agent" not in config.agent_names
    assert "connector_evaluate_agent" not in config.agent_names
    assert "bash_tool" in config.tool_names
    assert "evolution_tool" not in config.tool_names
    assert "run_skill" in config.skill_names
    assert "self_evolving_skill" not in config.skill_names
    assert config.connector_names == []
    assert config.env_names == []


sys.path.append(str(Path(root) / "examples"))

import run_programbench as rp  # noqa: E402


def test_select_instances_by_task_ids():
    instances = [
        {"instance_id": "a", "repository": "repo-a"},
        {"instance_id": "b", "repository": "repo-b"},
        {"instance_id": "c", "repository": "repo-c"},
    ]
    selected, warnings = rp.select_instances(instances, task_ids=["c", "a"])
    assert [i["instance_id"] for i in selected] == ["c", "a"]
    assert warnings == []


def test_select_instances_by_task_ids_skips_unknown():
    instances = [{"instance_id": "a"}, {"instance_id": "b"}]
    selected, warnings = rp.select_instances(instances, task_ids=["a", "does-not-exist"])
    assert [i["instance_id"] for i in selected] == ["a"]
    assert warnings == ["unknown task id(s) skipped: ['does-not-exist']"]


def test_select_instances_by_range():
    instances = [{"instance_id": str(i)} for i in range(10)]
    selected, warnings = rp.select_instances(instances, start=2, end=5)
    assert [i["instance_id"] for i in selected] == ["2", "3", "4"]
    assert warnings == []


def test_select_instances_task_ids_take_precedence_over_range():
    instances = [{"instance_id": "a"}, {"instance_id": "b"}]
    selected, warnings = rp.select_instances(instances, task_ids=["b"], start=0, end=1)
    assert [i["instance_id"] for i in selected] == ["b"]
    assert warnings == ["--start/--end ignored because --task-ids was given"]


def test_select_instances_requires_a_selector():
    with pytest.raises(ValueError):
        rp.select_instances([{"instance_id": "a"}])


def test_build_task_content_includes_system_prompt_and_fields():
    instance = {
        "repository": "abishekvashok/cmatrix",
        "language": "c",
        "image_name": "programbench/abishekvashok_1776_cmatrix.5c082c6",
        "commit": "5c082c6",
    }
    content = rp.build_task_content(instance)
    assert rp.SYSTEM_PROMPT.strip() in content
    assert "abishekvashok/cmatrix" in content
    assert "language: c" in content
    assert "programbench/abishekvashok_1776_cmatrix.5c082c6" in content
    assert "commit `5c082c6`" in content


def test_extend_roster_for_evolve_off_is_unchanged():
    agents, tools, skills = rp.extend_roster_for_evolve(
        ["meta_agent"], ["bash_tool"], ["run_skill"], evolve=False,
    )
    assert agents == ["meta_agent"]
    assert tools == ["bash_tool"]
    assert skills == ["run_skill"]


def test_extend_roster_for_evolve_on_adds_triads():
    agents, tools, skills = rp.extend_roster_for_evolve(
        ["meta_agent"], ["bash_tool"], ["run_skill"], evolve=True,
    )
    assert "tool_optimize_agent" in agents
    assert "connector_evaluate_agent" in agents
    assert len(agents) == 1 + len(rp.EVOLVE_AGENT_NAMES)
    assert "evolution_tool" in tools
    assert "self_evolving_skill" in skills
    assert "agent_creator_skill" in skills


def test_extend_roster_for_evolve_does_not_mutate_input_lists():
    base_agents = ["meta_agent"]
    rp.extend_roster_for_evolve(base_agents, [], [], evolve=True)
    assert base_agents == ["meta_agent"]


def test_parse_args_requires_a_selector():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py"]
    try:
        with pytest.raises(SystemExit):
            rp.parse_args()
    finally:
        sys.argv = old_argv


def test_parse_args_evolve_defaults_to_true():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--start", "0", "--end", "1"]
    try:
        args = rp.parse_args()
    finally:
        sys.argv = old_argv
    assert args.evolve is True


def test_parse_args_no_evolve_flag():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--start", "0", "--end", "1", "--no-evolve"]
    try:
        args = rp.parse_args()
    finally:
        sys.argv = old_argv
    assert args.evolve is False


def test_parse_args_task_ids():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--task-ids", "a,b, c"]
    try:
        args = rp.parse_args()
    finally:
        sys.argv = old_argv
    assert args.task_ids == "a,b, c"
