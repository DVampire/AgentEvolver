from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .tools.bash import bash_tool
    from .tools.job_list import job_list_tool
    from .tools.job_output import job_output_tool
    from .tools.job_kill import job_kill_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .agents.optimize_agent import optimize_agent
    from .agents.general_agent import general_agent
    from .memory.file_system_memory import file_system_memory

tag = "optimize_agent"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
project_root = "output/local"
log_path = "agent.log"

model_name = "llm_hub/claude-opus-5"

tool_names = [
    "bash_tool",
    "job_list_tool",
    "job_output_tool",
    "job_kill_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "glob_search_tool",
    "grep_search_tool",
    "inspect_tool",
]
agent_names = [
    "optimize_agent",
    # The replay smoke gate drives this as its probe before a newly registered
    # component is accepted. Absent, every registration is rejected with
    # "probe agent 'general_agent' not registered" after all the work is done.
    "general_agent",
]
skill_names = [
    # This agent's own guide, covering all eight component types; it reads the reference
    # file for whichever `target_type` it was dispatched with.
    "optimize_skill",
]
connector_names = []
memory_names = [
    "file_system_memory",
]

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    enable_evolving=False,
)

#-----------------MEMORY CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    enable_evolving=False,
)

#-----------------AGENT CONFIG-----------------
optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

general_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
)
