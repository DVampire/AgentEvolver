"""Config for examples/run_programbench.py.

Structured like `configs/meta_agent.py`, trimmed to what a ProgramBench
code-reconstruction run actually needs: the basic tools, the actor agents, and
the self-evolution roster that applies here — the tool / agent / skill triads,
`evolution_tool`, their creator skills, and `self_evolving_skill`.
`examples/run_programbench.py --no-evolve` strips the evolution roster back out
for a lean, cheaper "just do the task" run.

Everything else meta_agent.py carries is deliberately absent, because the task
is offline binary reconstruction inside a container:

- **The file/git tools are sandbox-aware, so they are included.**
  `read_file_tool`/`write_file_tool`/`edit_file_tool`/`list_dir_tool`/`git_tool`
  each read `ctx.extra["sandbox"]` and route their IO into the bound container.
  They were excluded while that was not the case: a write would have landed on the
  (nearly empty) host workspace instead of `/workspace`, giving the agent an
  inconsistent view of its own environment and letting its source go missing from
  `extract_submission()`'s tar. That also depends on the sandbox handle reaching
  sub-agents at all — see `protocol_manager.delegate`, which used to drop it.
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

# Same model, reached through OpenRouter rather than Google directly: the
# `google/*` route reads GOOGLE_API_KEY straight from the environment, and a
# direct-Google key that stops working takes the whole run down silently (every
# _think call 400s, the agent burns its step budget producing no tool calls, and
# still reports `done`). Override per run with
# `--cfg-options model_name=<name>`.
model_name = "openrouter/gemini-3.1-pro-preview"

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
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
    "deploy_tool",
    "evolution_tool",
    "escalate_tool",
    "reply_tool"
]

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
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

general_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

reviewer_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

#-----------------GENERATOR AGENT CONFIGS-----------------
tool_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

agent_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

skill_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

#-----------------OPTIMIZER AGENT CONFIGS-----------------
tool_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

agent_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

skill_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

#-----------------EVALUATOR AGENT CONFIGS-----------------
tool_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

agent_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

skill_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)

#-----------------META AGENT CONFIG-----------------
meta_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # ProgramBench needs a long leash: reconstruction is flag-by-flag differential
    # work, and a run that ran out of steps at 28/30 was told "budget CRITICAL, wrap
    # up" while it still had real work left. Steps are meant to be the binding
    # constraint here, so the wall clock and token budget are raised to match —
    # at the measured ~9s/step, 200 steps is ~30min, and latency grows with context,
    # so 1800s would have quietly become the new limit at around step 120.
    max_step=200,
    timeout=5400,
    max_token=3000000,
)
