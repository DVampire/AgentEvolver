"""Control arm for the ProgramBench self-evolution experiment.

Identical to `configs/programbench_agent.py` except that self-evolution is absent
rather than switched off. Three differences, and only three:

| | programbench_agent.py | this file |
|---|---|---|
| `agent_names` | 4 actors + generate/optimize/evaluate | 4 actors |
| `tool_names` | basic + `evolution_tool` | basic |
| `skill_names` | `self_evolving_skill` | none |

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
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.code_interpreter import code_interpreter_tool
    from .tools.escalate import escalate_tool
    from .memory.file_system_memory import file_system_memory

tag = "programbench_agent_baseline"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
project_root = "output/local"
log_path = "agent.log"

# Same model and route as the evolving arm — see programbench_agent.py for why
# this goes through OpenRouter rather than a direct vendor provider. The two arms
# differ only in the evolution roster, so this must stay equal to the evolving
# arm's model_name; a mismatch turns the comparison into a model benchmark.
# A registered model_manager name, not a litellm model string. The registry already maps
# it to the upstream id (`openrouter/claude-opus-5` -> `anthropic/claude-opus-5`),
# so the vendor prefix belongs there and not here. Calling litellm directly with the
# prefixed spelling appears to work and is a different code path; going through the
# manager with an unregistered name fails every `_think` instantly instead.
model_name = "llm_hub/claude-opus-5"

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
]

# Empty, and that is what the arm measures: what the tool roster alone achieves. Worth
# reading beside the score — the first run with an empty skill list scored 53 on cmatrix
# and the run that added five verification skills scored 92, though those runs also
# differed in model route and prompt version, so the gap is not the skills' alone.
#
# Whatever goes here must go in the evolving arm too, minus `self_evolving_skill`. The
# two arms differ in evolution capability and nothing else; a skill in one and not the
# other turns the comparison into a skill benchmark.
skill_names = []

env_names = [
    "terminal",
    "job",
]

# Absent means *all*, not none: `plugin_manager.initialize(None)` builds every
# registered plugin, and `agentevolver/plugins/` is 517 files. Stating the empty list
# is what actually turns them off.
plugin_names = []
connector_names = []
workflow_names = []

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
# A worker step does the work — read the binary's behaviour, write source, compile. A
# MetaAgent step is a single dispatch: "run general_agent", "now reviewer_agent". Sharing
# one 100 made the coordinator run out first: on `zoxide` it dispatched 93 times
# (general -> reviewer -> code, iterating on flag-parsing differences) and hit its own
# ceiling with the workers still mid-reconstruction, so the run ended "not completed"
# while the actual work was progressing. The worker ceiling bounds a single
# reconstruction; the coordinator ceiling is how many rounds of delegation it may run,
# and is several times larger because each round costs it one step.
WORKER_MAX_STEP = 200
META_MAX_STEP = 400
WALL_CLOCK = 21600
# Not an official number — the official harness caps per-instance *cost*, a different
# axis. This is a cumulative-token runaway guard, left high enough that steps or wall
# clock bind first.
MAX_TOKEN = 3000000

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
