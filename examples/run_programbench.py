"""Run MetaAgent on selected ProgramBench tasks, modeled on run_meta_agent.py.

Runs tasks only — no scoring. See agentevolver/benchmark/default/programbench.py
for the existing `programbench eval`-based scorer, which can be pointed at this
script's output workspaces separately.

The agent works inside the plain filesystem session sandbox — no per-instance
Docker binding yet (image_name is only referenced in the task prompt text). See
docs/superpowers/specs/2026-07-20-programbench-runner-design.md §10 for the
`sandbox_manager.acquire("opensandbox", image=...)` follow-up.

Usage
-----
# Run two named instances, with self-evolution on (default)
python examples/run_programbench.py --task-ids <instance_id_1>,<instance_id_2>

# Run the first 5 instances (by load order), self-evolution off
python examples/run_programbench.py --start 0 --end 5 --no-evolve

# Override config options
python examples/run_programbench.py --start 0 --end 1 --cfg-options model_name=openai/o3
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(verbose=True)

from mmengine import DictAction

root = str(Path(__file__).resolve().parents[1])
sys.path.append(root)

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.model import model_manager
from agentevolver.version import version_manager
from agentevolver.prompt import prompt_manager
from agentevolver.memory import memory_manager
from agentevolver.tool import tool_manager
from agentevolver.skill import skill_manager
from agentevolver.connector import connector_manager
from agentevolver.agent import agent_manager
from agentevolver.extension import extension_manager
from agentevolver.hook import hook_manager
from agentevolver.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.session.types import SessionContext
from agentevolver.session.project import ensure_session_sandbox, bind_session_roots
from agentevolver.utils import make_id, dedent
from agentevolver.benchmark.default.programbench import SYSTEM_PROMPT


EVOLVE_AGENT_NAMES = [
    "tool_optimize_agent", "tool_evaluate_agent", "tool_generate_agent",
    "agent_generate_agent", "agent_optimize_agent", "agent_evaluate_agent",
    "skill_generate_agent", "skill_optimize_agent", "skill_evaluate_agent",
    "environment_generate_agent", "environment_optimize_agent", "environment_evaluate_agent",
    "connector_generate_agent", "connector_optimize_agent", "connector_evaluate_agent",
]
EVOLVE_TOOL_NAMES = ["evolution_tool"]
EVOLVE_SKILL_NAMES = [
    "self_evolving_skill",
    "agent_creator_skill", "tool_creator_skill", "environment_creator_skill",
    "skill_creator_skill", "connector_creator_skill",
]


def extend_roster_for_evolve(agent_names, tool_names, skill_names, evolve):
    """Extend the base roster with the self-evolution add-ons when `evolve` is True.

    Returns fresh (agent_names, tool_names, skill_names) lists; the inputs are
    never mutated in place.
    """
    agent_names = list(agent_names)
    tool_names = list(tool_names)
    skill_names = list(skill_names)
    if evolve:
        agent_names += [n for n in EVOLVE_AGENT_NAMES if n not in agent_names]
        tool_names += [n for n in EVOLVE_TOOL_NAMES if n not in tool_names]
        skill_names += [n for n in EVOLVE_SKILL_NAMES if n not in skill_names]
    return agent_names, tool_names, skill_names


def select_instances(instances, task_ids=None, start=None, end=None):
    """Select a subset of ProgramBench instances by id list and/or index range.

    `task_ids` takes precedence over `start`/`end` (a warning is returned, not
    raised, if both are given). Unknown ids are skipped, also as a warning.
    Raises ValueError if neither selector is given.

    Returns (selected: list[dict], warnings: list[str]).
    """
    if not task_ids and start is None and end is None:
        raise ValueError("select_instances requires task_ids or start/end")

    warnings = []
    if task_ids:
        if start is not None or end is not None:
            warnings.append("--start/--end ignored because --task-ids was given")
        by_id = {inst["instance_id"]: inst for inst in instances}
        selected = [by_id[tid] for tid in task_ids if tid in by_id]
        unknown = [tid for tid in task_ids if tid not in by_id]
        if unknown:
            warnings.append(f"unknown task id(s) skipped: {unknown}")
        return selected, warnings

    return list(instances[start:end]), warnings


def build_task_content(instance):
    """Build the MetaAgent task content for one ProgramBench instance.

    Folds ProgramBenchmark's SYSTEM_PROMPT into the content (agent_manager has
    no separate system-prompt override hook here, matching how
    run_meta_agent.py / run_hle.py already thread only task content through).
    """
    question = dedent(f"""
        Reconstruct the program `{instance.get('repository', '')}` (language: {instance.get('language', '')}).

        A compiled binary and its documentation are available in your sandbox
        (task image: `{instance.get('image_name', '')}`, commit `{instance.get('commit', '')}`). Implement a complete
        codebase that reproduces the program's behavior. Produce the full source
        tree as your final answer.
    """)
    return f"{SYSTEM_PROMPT}\n\n{question}"
