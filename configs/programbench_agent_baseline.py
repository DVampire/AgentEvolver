"""Control arm for the ProgramBench self-evolution experiment.

Identical to `configs/programbench_agent.py` except that self-evolution is absent
rather than switched off. Three differences, and only three:

| | programbench_agent.py | this file |
|---|---|---|
| `agent_names` | 4 actors + 9 generate/optimize/evaluate agents | 4 actors |
| `tool_names` | basic + `evolution_tool` | basic |
| `skill_names` | `self_evolving_skill` + 3 creators | none |

This arm carries no skills at all, so it measures what the tool roster alone
achieves. Worth knowing when reading its score: the first run with an empty skill
list scored 53 on cmatrix, and the run that added the five verification skills
scored 92 — though those two runs also differed in model route and prompt
version, so that gap is not attributable to the skills alone.

Why a second file instead of a `--no-evolve` flag on the shared config: the flag
stripped names at runtime, so the roster a result came from lived in a command
line rather than in the repo, and nothing stopped `config A + --no-evolve` from
producing a third state that matched neither arm. Each arm is now a file you can
read, diff, and re-run.

Note that the *prompt* follows the roster automatically —
`Agent._evolution_enabled()` derives it from the live roster, so under this config
meta_agent's `<self-evolution-rules>` block and the
generator/optimizer/evaluator taxonomy are not rendered at all. That is a real
difference between the arms beyond capability availability: this arm's system
prompt is ~29% shorter. It cannot be separated from the roster change, and any
report comparing the two must say so.
"""
from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .agents.general_agent import general_agent
    from .agents.reviewer_agent import reviewer_agent
    from .tools.bash import bash_tool
    from .tools.code_interpreter import code_interpreter_tool
    from .tools.escalate import escalate_tool
    from .memory.file_system_memory import file_system_memory

tag = "programbench_agent_baseline"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label,
# not a directory level, so it cannot collide with an owner name.
project_root = "output/.runtime/unbound"
log_path = "agent.log"

# Same model and route as the evolving arm — see programbench_agent.py for why
# this goes through OpenRouter rather than the direct `google/*` provider.
model_name = "openrouter/gemini-3.1-pro-preview"

memory_names = [
    "file_system_memory",
]

# Actors only. No generate/optimize/evaluate triads.
agent_names = [
    "meta_agent",
    "code_agent",
    "general_agent",
    "reviewer_agent",
]

# No `evolution_tool` — that is what makes this the control arm, and
# Agent._evolution_enabled() keys off it, so leaving it in would render the
# evolution rules into the prompt of a run that is supposed to be without them.
#
# The file/git tools ARE included: read_file/write_file/edit_file/list_dir/git all
# read `ctx.extra["sandbox"]` and route their IO into the bound container. They
# were excluded while that was not true — a write would have landed on the host
# and gone missing from extract_submission()'s tar of the container.
tool_names = [
    # sandbox-aware: each of these reads ctx.extra["sandbox"] and routes its IO
    # into the bound peer container, so they see the task's /workspace.
    "bash_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
    "grep_search_tool",
    "glob_search_tool",
    # control plane — no filesystem of their own
    "done_tool",
    "escalate_tool",
    "reply_tool",
    "todo_tool",
    "code_interpreter_tool",
]

# Verification methodology, present in both arms.
skill_names = [
]

connector_names = []
env_names = []

#-----------------TOOL CONFIGS-----------------
bash_tool.update(enable_evolving=False)

# One-shot instead of kernel: the task fixture lives in a peer cleanroom, and a
# kernel started in the base container cannot see it. A run here asked the
# interpreter to rewrite print_help()/print_version() in /workspace/cmatrix.c —
# exactly the fix for this benchmark's largest failure class — and got
# FileNotFoundError. Without the kernel the script runs inside the peer, at the cost
# of no cross-call state and no captured figures (neither matters for this task).
code_interpreter_tool.update(use_kernel=False)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
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
