"""Config for examples/run_programbench.py.

Structured like `configs/meta_agent.py`, trimmed to what a ProgramBench
code-reconstruction run actually needs: the basic tools, the actor agents, and
the self-evolution roster that applies here — the tool / agent / skill triads,
`evolution_tool`, their creator skills, and `self_evolving_skill`.
`examples/run_programbench.py --no-evolve` strips the evolution roster back out
for a lean, cheaper "just do the task" run.

Everything else meta_agent.py carries is deliberately absent, because the task
is offline binary reconstruction inside a container:

- **Only `bash_tool` for file/git work.** `bash_tool` is the ONLY tool in this
  repo that checks `get_current_sandbox()` (agentevolver/tool/default/bash.py)
  and routes into the bound Docker sandbox — confirmed by grepping the codebase
  for `get_current_sandbox`. `read_file_tool`/`write_file_tool`/`edit_file_tool`/
  `list_dir_tool`/`git_tool` only run `check_session_path`, a *host* filesystem
  boundary check (agentevolver/sandbox/project.py), so all five would silently
  operate on the (nearly empty) host workspace instead of the container's
  `/workspace` — giving the agent an inconsistent view of its own environment and
  letting writes go missing from `extract_submission()`'s tar. This matches the
  official mini-swe-agent ProgramBench baseline, which also gives the agent one
  bash tool and nothing else.
- **No `monitor_agent`.** It spawns its own `asyncio.create_subprocess_shell`,
  bypassing the sandbox entirely.
- **No web tools, browser agent, or browser environment.** The run is offline by
  design — network isolation is the benchmark's anti-cheat mechanism, and giving
  the agent retrieval would let it fetch the original source it is supposed to
  reconstruct.
- **No environment / memory / connector triads.** A reconstruction task has no
  environment and no connector to evolve, and swapping the memory system
  mid-benchmark changes the measurement rather than the solution. Evolution here
  is scoped to what the agent actually uses: its tools, its sub-agents, and its
  skills.
- **No document / science / general-workflow skills.** Nothing in a
  binary-reconstruction task consumes them; they would only inflate the prompt.
"""
from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .agents.general_agent import general_agent
    from .agents.reviewer_agent import reviewer_agent
    from .agents.tool_generate_agent import tool_generate_agent
    from .agents.tool_optimize_agent import tool_optimize_agent
    from .agents.tool_evaluate_agent import tool_evaluate_agent
    from .agents.agent_generate_agent import agent_generate_agent
    from .agents.agent_optimize_agent import agent_optimize_agent
    from .agents.agent_evaluate_agent import agent_evaluate_agent
    from .agents.skill_generate_agent import skill_generate_agent
    from .agents.skill_optimize_agent import skill_optimize_agent
    from .agents.skill_evaluate_agent import skill_evaluate_agent
    from .tools.bash import bash_tool
    from .tools.evolution import evolution_tool
    from .tools.escalate import escalate_tool
    from .memory.file_system_memory import file_system_memory

tag = "programbench_agent"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label,
# not a directory level, so it cannot collide with an owner name.
project_root = "output/.runtime/unbound"
log_path = "agent.log"

model_name = "google/gemini-3.1-pro-preview"

memory_names = [
    "file_system_memory",
]

agent_names = [
    # actors
    "meta_agent",
    "code_agent",
    "general_agent",
    "reviewer_agent",
    # self-evolution triads — stripped by run_programbench.py --no-evolve
    "tool_generate_agent",
    "tool_optimize_agent",
    "tool_evaluate_agent",
    "agent_generate_agent",
    "agent_optimize_agent",
    "agent_evaluate_agent",
    "skill_generate_agent",
    "skill_optimize_agent",
    "skill_evaluate_agent",
]

# Basic tools only. bash_tool is the agent's entire hand for the task itself —
# see the module docstring for why the other file/git tools are excluded.
tool_names = [
    "bash_tool",
    "done_tool",
    "escalate_tool",
    "reply_tool",
    # self-evolution — stripped by --no-evolve
    "evolution_tool",
]

# Self-evolution skills only: the playbook plus one creator skill per evolvable
# component type. Stripped by --no-evolve.
skill_names = [
    # global playbook: WHEN to evolve, the loop, and the enable_evolving gate
    "self_evolving_skill",
    # per-type creator skills (orchestrator role) — drive each triad's
    # create -> evaluate -> improve loop
    "tool_creator_skill",
    "agent_creator_skill",
    "skill_creator_skill",
]

connector_names = []
env_names = []

#-----------------TOOL CONFIGS-----------------
# permission_mode is already "danger_full_access" at the tool's own base default
# (configs/tools/bash.py) — no need to restate it here, matching meta_agent.py/hle.py.
bash_tool.update(enable_evolving=False)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    # Not evolvable here (meta_agent.py does allow it): the memory system is part
    # of what a benchmark run measures, so rewriting it mid-run would change the
    # measurement rather than the solution.
    enable_evolving=False,
)

#-----------------ACTOR AGENT CONFIGS-----------------
code_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

general_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

reviewer_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

#-----------------GENERATOR AGENT CONFIGS-----------------
tool_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

agent_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

skill_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

#-----------------OPTIMIZER AGENT CONFIGS-----------------
tool_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

agent_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

skill_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

#-----------------EVALUATOR AGENT CONFIGS-----------------
tool_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

agent_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

skill_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

#-----------------META AGENT CONFIG-----------------
meta_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    max_step=50,
)
