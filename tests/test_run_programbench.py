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
