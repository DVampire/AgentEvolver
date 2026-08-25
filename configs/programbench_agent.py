"""Config for examples/run_programbench.py.

Structured like `configs/meta_agent.py`, trimmed to what a ProgramBench
code-reconstruction run actually needs: the basic tools, the actor agents, and
the self-evolution roster that applies here — the tool / agent / skill triads,
`evolution_tool`, and `self_evolving_skill`.
`examples/run_programbench.py --no-evolve` strips the evolution roster back out
for a lean, cheaper "just do the task" run.

Everything else meta_agent.py carries is deliberately absent, because the task
is offline binary reconstruction inside a container:

- **The file/git tools are included, and they are not container-aware.**
  `read_file_tool`/`write_file_tool`/`edit_file_tool`/`list_dir_tool`/`git_tool`
  read and write wherever the agent process is running. Nothing consults a sandbox
  handle. What makes them land in `/workspace` is that the agent itself runs
  *inside* the task container — `examples/run_programbench.py` starts a launcher on
  the host, which starts the agent in the container, and from there the local
  filesystem simply is the task's.
  This matters because the alternative was tried and failed: while the tools ran on
  the host, a write landed on the (nearly empty) host workspace instead of
  `/workspace`, giving the agent an inconsistent view of its own environment and
  letting its source go missing from `extract_submission()`'s tar.
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
    from .agents.generate_agent import generate_agent
    from .agents.optimize_agent import optimize_agent
    from .agents.evaluate_agent import evaluate_agent
    from .tools.bash import bash_tool
    from .tools.batch_call import batch_call_tool
    from .tools.ask_user import ask_user_question
    from .tools.exit_plan_mode import exit_plan_mode
    from .tools.code_interpreter import code_interpreter_tool
    from .tools.evolution import evolution_tool
    from .tools.escalate import escalate_tool
    from .memory.file_system_memory import file_system_memory

tag = "programbench_agent"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
project_root = "output/local"
log_path = "agent.log"

# Reached through OpenRouter rather than a vendor's direct route: a `google/*` or
# `anthropic/*` provider reads its key straight from the environment, and a direct
# key that stops working takes the whole run down silently (every _think call 400s,
# the agent burns its step budget producing no tool calls, and still reports
# `done`). Override per run with `--cfg-options model_name=<name>` — that override
# does reach the container, but only since the launcher started forwarding it; it
# was dropped silently before, so a run pinned to a model this way is worth
# checking against the container's own command line.
# A registered model_manager name, not a litellm model string. The registry already maps
# it to the upstream id (`openrouter/claude-opus-5` -> `anthropic/claude-opus-5`),
# so the vendor prefix belongs there and not here. Calling litellm directly with the
# prefixed spelling appears to work and is a different code path; going through the
# manager with an unregistered name fails every `_think` instantly instead.
# Keep this in sync with programbench_agent_baseline.py — the two arms are only
# comparable while they run the same model.
model_name = "llm_hub/deepseek-v4-flash"

memory_names = [
    "file_system_memory",
]

agent_names = [
    # actors
    "meta_agent",
    "code_agent",
    "general_agent",
    "reviewer_agent",
    # self-evolution: one agent per role, each building whichever component type it is
    # told to. This was nine — one per (role x type) — and the type is an input to the
    # run rather than a different agent.
    "generate_agent",
    "optimize_agent",
    "evaluate_agent",
]

# Basic tools only — see the module docstring for what is deliberately absent.
tool_names = [
    # Read, write, look around, run something.
    "bash_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "escalate_tool",
    "reply_tool",
    "report_tool",
    "done_tool",
    "code_interpreter_tool",
    "evolution_tool",
]

skill_names = [
    # WHEN to evolve, the loop, the enable_evolving gate, and how to write each of the
    # eight component types. This was four entries: the playbook plus a creator skill
    # per type.
    "self_evolving_skill",
]

connector_names = []
env_names = [
    # Both were replacements for tools this config already had: the six terminal
    # tools, and the three job tools. As environments they also put what each
    # open terminal shows, and what is still running, in front of the agent every
    # step rather than only when it asks.
    "terminal",
    "job",
]

#-----------------SANDBOX EGRESS-----------------
# The benchmark's anti-cheat is that the agent's shell cannot reach the internet: it must
# reconstruct the program from the binary's behaviour, not fetch the original source. The
# sandbox therefore runs with no network interface, and the only reachable hosts are the
# model endpoints the agent brain needs — read from the environment rather than hardcoded,
# because a deployment that points a provider at its own gateway is exactly the case that
# an allowlist naming the public host would silently break.
#
# `network=False` plus this allowlist means an unlisted host is not filtered, it is
# unreachable: there is no interface, and the one route out is a relay on the host whose
# decisions the sandbox cannot influence. Verified live — `git clone https://github.com/...`
# returns "HTTP code 403 from proxy after CONNECT", and a raw connect to 1.1.1.1 returns
# "Network is unreachable".
# Declared, not computed: the endpoints are derived by the sandbox manager from the
# `*_API_BASE` variables in the environment. A config file cannot do that itself —
# mmengine parses configs with lazy imports, so calling an imported function here raises.
sandbox_allow_model_endpoints = True
sandbox_allow_hosts = []
# Belt and braces on top of the allowlist. Redundant while the network is closed, and
# deliberately kept: it keeps the intent readable, and it still holds if someone opens the
# network to debug a run.
sandbox_deny_hosts = [
    "github.com", "*.github.com", "raw.githubusercontent.com", "codeload.github.com",
    "gitlab.com", "*.gitlab.com", "bitbucket.org", "*.bitbucket.org",
    "pypi.org", "*.pypi.org", "files.pythonhosted.org",
    "crates.io", "*.crates.io", "registry.npmjs.org", "proxy.golang.org",
]

#-----------------BUDGET (aligned to the official harness)-----------------
# 100 steps, from measurement rather than from the official ceiling. Every run so far
# reached its best alignment inside the first ~120 steps and then plateaued: one hit 6 of
# 8 flags at step 53 and spent the next 240 steps eliminating one more; another was
# effectively finished at step 63 and spent 650 further steps circling two differences,
# one of them unreachable, before deleting its way back to roughly what it had. The tail
# of a long budget is not where the score comes from.
#
# The official harness allows 1000. Running below that cannot flatter our numbers — a
# tighter budget can only cost score — so a comparison against the leaderboard stays
# honest, and the wall clock drops from four hours per instance to well under one.
#
# Caveat worth stating plainly: these are *per-agent* ceilings, and a MAS spends steps
# across a MetaAgent plus its actors, so they are not a like-for-like total. Matching
# the ceiling is what stops us self-handicapping; matching the total is not achievable
# without global step accounting. run_programbench.py therefore records the steps
# actually consumed per instance into results.json, so a run that spent more than the
# official allowance is visible rather than hidden.
#
# Expect longer runs: the task document's stopping rule spends up to two thirds of the
# budget exploring before it starts converging, so a 1000-step ceiling is a much longer
# leash than the ~1h that 200-step runs took.
#
# Two ceilings, not one, because a MetaAgent step and a worker step are not the same unit.
# A worker step does the work; a MetaAgent step is a single dispatch. Sharing one 100 let
# the coordinator run out first while the workers were still mid-reconstruction (measured
# on the baseline arm's `zoxide` run: 93 dispatches, meta ceiling hit, run ended "not
# completed" with work still progressing). Kept identical to the baseline arm — the two
# arms must differ only in the evolution roster.
WORKER_MAX_STEP = 200
META_MAX_STEP = 400
WALL_CLOCK = 21600
# Not an official number — the official harness caps per-instance *cost*, a different
# axis. This is a cumulative-token runaway guard, left high enough that steps or wall
# clock bind first.
MAX_TOKEN = 3000000

#-----------------TOOL CONFIGS-----------------
# permission_mode is already "danger_full_access" at the tool's own base default
# (configs/tools/bash.py) — no need to restate it here, matching meta_agent.py/hle.py.
bash_tool.update(enable_evolving=False)

# The Jupyter kernel would start in the base container, where the task's /workspace
# does not exist — a run asked the interpreter to rewrite a file under /workspace and
# got FileNotFoundError. Without the kernel the script is written into the peer and
# run there, at the cost of no cross-call state and no captured figures (neither
# matters here). Same setting as the baseline arm.
code_interpreter_tool.update(use_kernel=False)

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
    max_step=WORKER_MAX_STEP,
    timeout=WALL_CLOCK,
    max_token=MAX_TOKEN,
)

general_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    max_step=WORKER_MAX_STEP,
    timeout=WALL_CLOCK,
    max_token=MAX_TOKEN,
)

reviewer_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    max_step=WORKER_MAX_STEP,
    timeout=WALL_CLOCK,
    max_token=MAX_TOKEN,
)

#-----------------EVOLUTION AGENT CONFIGS-----------------
_EVOLUTION = dict(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    max_step=WORKER_MAX_STEP,
    timeout=WALL_CLOCK,
    max_token=MAX_TOKEN,
)
generate_agent.update(**_EVOLUTION)
optimize_agent.update(**_EVOLUTION)
evaluate_agent.update(**_EVOLUTION)

#-----------------META AGENT CONFIG-----------------
meta_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    max_step=META_MAX_STEP,
    timeout=WALL_CLOCK,
    max_token=MAX_TOKEN,
)
