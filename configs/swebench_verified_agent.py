"""SWE-bench Verified configuration.

Agents verify locally; the host evaluates their frozen patch only after exit.
The agent and baseline arms differ in the self-evolution roster, not grading access.
"""
from mmengine.config import read_base

with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.generate_agent import generate_agent
    from .agents.optimize_agent import optimize_agent
    from .agents.evaluate_agent import evaluate_agent
    from .tools.bash import bash_tool
    from .tools.adoption import adoption_tool
    from .memory.file_system_memory import file_system_memory

tag = "swebench_verified_agent"
# ``project_root`` is derived from tag by the PathManager. The two experiment arms use
# separate output namespaces while keeping every other runtime setting comparable.
log_path = "agent.log"

# A registered model_manager name (not a litellm string). Keep in sync with
# swebench_verified_agent_baseline.py — the two arms are only comparable on one model.
# Override per run with `--cfg-options model_name=<name>`.
model_name = "llm_hub/claude-opus-5"

memory_names = [
    "file_system_memory",
]
agent_names = [
    # ONE actor. The MetaAgent does the whole task itself (read the codebase, write the
    # fix, iterate against the grader), the way the reference bash-only SWE agents do.
    "meta_agent",
    # self-evolution roster — the ONLY difference from the baseline arm.
    "generate_agent",
    "optimize_agent",
    "evaluate_agent",
]
tool_names = [
    "bash_tool",
    "done_tool",
    # Ask the real hidden suite to score the current patch and return which tests still
    # fail (NAMES + counts only, never a test body or expected output). Host-mediated,
    # rate-limited. Present in both arms, so it does not affect the evolution difference.
    "adoption_tool",
]
skill_names = [
    "self_evolving_skill",
]
connector_names = []
env_names = [
    "job",
]

sandbox_allow_model_endpoints = True
sandbox_allow_hosts = []
# Anti-cheat: the fix lives in the upstream repo's later commits, so block the agent from
# fetching it (git remotes) or pulling source from package registries. Deps are baked into
# the instance image, so the run needs no network beyond the model endpoint.
sandbox_deny_hosts = [
    "github.com", "*.github.com", "raw.githubusercontent.com", "codeload.github.com",
    "gitlab.com", "*.gitlab.com", "bitbucket.org", "*.bitbucket.org",
    "pypi.org", "*.pypi.org", "files.pythonhosted.org",
    "crates.io", "*.crates.io", "registry.npmjs.org", "proxy.golang.org",
]

WORKER_MAX_STEP = 50
META_MAX_STEP = 400
WALL_CLOCK = 7200
MAX_TOKEN = 3000000

bash_tool.update(enable_evolving=False)

file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    enable_evolving=False,
    # Same cache-aware memory tuning as ProgramBench: cap a recorded entry at 2500 chars,
    # hold 6 recent records, carry 8 compacted long-term summaries. Must stay identical to
    # the baseline arm.
    record_detail_max=2500,
    recent_fetch=6,
    working_fetch=8,
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
    retain_recent_steps=4,
    compact_after_steps=24,
    compact_body_tokens=100000,
)
