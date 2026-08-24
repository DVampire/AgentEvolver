"""HLE (Humanity's Last Exam) evaluation config.

Composes the base settings + a general answering agent + the HLE benchmark. Used by
``examples/run_hle.py``. The answering pipeline is the current framework's `general_agent`
(the old bus/planner/opencode pipeline the guide referenced is not part of this repo).
"""
from mmengine.config import read_base

with read_base():
    from .base import memory_config, window_size, max_tokens, model_roles
    from .agents.general_agent import general_agent
    from .tools.bash import bash_tool
    from .tools.batch_call import batch_call_tool
    from .tools.read_image import read_image_tool
    from .tools.ask_user import ask_user_question
    from .tools.exit_plan_mode import exit_plan_mode
    from .memory.file_system_memory import file_system_memory
    from .benchmarks.hle import hle_benchmark

tag = "hle"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
project_root = "output/local"
log_path = "hle.log"

version = "0.1.0"
model_name = "google/gemini-3.1-pro-preview"

memory_names = ["file_system_memory"]
agent_names = ["general_agent"]
env_names = [
    "job",
    # Terminals are an environment now: what each open one is showing arrives in
    # `environment-state` every step, instead of being fetched with a read tool.
    "terminal",
]

tool_names = [
    "bash_tool",
    "batch_call_tool",
    "read_image_tool",
    "ask_user_question",
    "exit_plan_mode",
    # background work — start something long with bash_tool(run_in_background),
    # then keep working and collect it instead of spending a step waiting
    "code_interpreter_tool",
    "done_tool",
    "inspect_tool",
    "todo_tool",
]
skill_names = []
connector_names = []
benchmark_names = ["hle"]

# -------- tools --------
bash_tool.update(enable_evolving=False)

# -------- memory --------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    enable_evolving=False,
)

# -------- answering agent --------
general_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=False,
)

# -------- benchmark --------
# start/end slice the 2500-question test split (overridable via --start/--end).
# model_name here is the LLM judge used to score each answer.
hle_benchmark.update(
    base_dir="benchmark/hle",
    model_name="google/gemini-3.1-pro-preview",
    start=0,
    end=None,
)
