"""Config for examples/run_programbench.py.

Base roster for a pure ProgramBench code-reconstruction run: MetaAgent +
code/general/reviewer/monitor actor agents, plus every optimizer/generator/
evaluator agent, `evolution_tool`, and `self_evolving_skill` are imported and
configured here too (so their settings exist) but are NOT in the default
`agent_names`/`tool_names`/`skill_names` lists below — the running script's
`extend_roster_for_evolve()` appends them at runtime when `--evolve` is set
(the default). No browser/connector/environment wiring — irrelevant to a
code-reconstruction task.
"""
from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .agents.general_agent import general_agent
    from .agents.reviewer_agent import reviewer_agent
    from .agents.monitor_agent import monitor_agent
    from .agents.tool_optimize_agent import tool_optimize_agent
    from .agents.tool_evaluate_agent import tool_evaluate_agent
    from .agents.tool_generate_agent import tool_generate_agent
    from .agents.agent_generate_agent import agent_generate_agent
    from .agents.agent_optimize_agent import agent_optimize_agent
    from .agents.agent_evaluate_agent import agent_evaluate_agent
    from .agents.skill_generate_agent import skill_generate_agent
    from .agents.skill_optimize_agent import skill_optimize_agent
    from .agents.skill_evaluate_agent import skill_evaluate_agent
    from .agents.environment_generate_agent import environment_generate_agent
    from .agents.environment_optimize_agent import environment_optimize_agent
    from .agents.environment_evaluate_agent import environment_evaluate_agent
    from .agents.connector_generate_agent import connector_generate_agent
    from .agents.connector_optimize_agent import connector_optimize_agent
    from .agents.connector_evaluate_agent import connector_evaluate_agent
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.git import git_tool
    from .tools.evolution import evolution_tool
    from .tools.escalate import escalate_tool
    from .memory.file_system_memory import file_system_memory

tag = "programbench_agent"
project_root = f"output/{tag}"
log_path = "agent.log"

model_name = "google/gemini-3.5-flash"

memory_names = [
    "file_system_memory",
]

# Base roster — extended at runtime by examples/run_programbench.py's
# extend_roster_for_evolve() when --evolve is set (default: on).
agent_names = [
    "meta_agent",
    "code_agent",
    "general_agent",
    "reviewer_agent",
    "monitor_agent",
]
tool_names = [
    "bash_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
    "escalate_tool",
    "reply_tool",
]
skill_names = [
    "code_review_skill",
    "security_review_skill",
    "simplify_skill",
    "review_skill",
    "verify_skill",
    "run_skill",
    "init_skill",
    "planning_and_task_breakdown_skill",
    "spec_driven_development_skill",
    "context_engineering_skill",
    "doubt_driven_development_skill",
    "test_driven_development_skill",
    "debugging_and_error_recovery_skill",
    "source_driven_development_skill",
    "api_and_interface_design_skill",
    "incremental_implementation_skill",
    "documentation_and_adrs_skill",
    "git_workflow_and_versioning_skill",
    "performance_optimization_skill",
    "observability_and_instrumentation_skill",
]
connector_names = []
env_names = []

#-----------------TOOL CONFIGS-----------------
# permission_mode is already "danger_full_access" at the tool's own base default
# (configs/tools/bash.py) — no need to restate it here, matching meta_agent.py/hle.py.
bash_tool.update(enable_evolving=False)
git_tool.update(timeout=60)

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

monitor_agent.update(
    enable_evolving=False,
)

#-----------------OPTIMIZER/GENERATOR/EVALUATOR AGENT CONFIGS (self-evolution roster)-----------------
tool_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

tool_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

tool_evaluate_agent.update(
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

agent_optimize_agent.update(
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

skill_generate_agent.update(
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

skill_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

environment_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

environment_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

environment_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

connector_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

connector_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

connector_evaluate_agent.update(
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
