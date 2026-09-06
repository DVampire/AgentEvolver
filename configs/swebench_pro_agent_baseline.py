"""Config for examples/run_swebench_pro.py — the NO-evolution (baseline) arm.

Identical to `configs/swebench_pro_agent.py` except the self-evolution roster is
removed. This is the control the evolution arm is measured against, so every other
value MUST stay in sync (see test_shipped_configs):

| field         | agent arm                                  | this baseline        |
| `agent_names` | meta_agent + generate/optimize/evaluate    | meta_agent           |
| `tool_names`  | bash + done + inspect + adoption           | bash + done + inspect |
| `skill_names` | self_evolving_skill                        | none                 |

The comparison is only meaningful while the two arms run the same model, the same
memory settings, and the same final-only grading protocol. Both arms verify locally;
neither receives hidden test scores while solving.

GT-SAFETY (critical): the launcher hands the agent ONLY
`problem_statement` / `requirements` / `interface`; the oracle fields
(`patch`, `test_patch`, `fail_to_pass`, `pass_to_pass`) never enter the container.
"""
from mmengine.config import read_base

with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .tools.bash import bash_tool
    from .memory.file_system_memory import file_system_memory

tag = "swebench_pro_agent_baseline"
# ``project_root`` is derived from tag by the PathManager. This keeps the baseline's
# sessions, request HTML and result summaries under one visible namespace.
log_path = "agent.log"

# Keep in sync with swebench_pro_agent.py — the two arms are only comparable on one model.
model_name = "llm_hub/claude-opus-5"

memory_names = [
    "file_system_memory",
]
agent_names = [
    "meta_agent",
]
tool_names = [
    "bash_tool",
    "done_tool",
    "inspect_tool",  # Same read-only capability discovery as the evolution arm.
]
# No self_evolving_skill here — that is the evolution arm's alone.
skill_names = []
connector_names = []
env_names = [
    "job",
]

sandbox_allow_model_endpoints = True
sandbox_allow_hosts = []
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
    record_detail_max=2500,
    recent_fetch=6,
    working_fetch=8,
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
    # Codex-style window: token growth after the checkpoint is primary; four exact
    # closed steps preserve enough local causality for portable provider fallbacks.
    retain_recent_steps=4,
    compact_after_steps=24,
    compact_body_tokens=100000,
)
