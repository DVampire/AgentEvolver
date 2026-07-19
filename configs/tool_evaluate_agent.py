from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .agents.tool_evaluate_agent import tool_evaluate_agent
    from .memory.file_system_memory import file_system_memory

tag = "tool_evaluate_agent"
project_root = f"output/{tag}"
log_root = f"{project_root}/log"
workspace_root = f"{project_root}/workspace"
log_path = "tool_evaluate_agent.log"

model_name = "openrouter/claude-opus-4.8"

tool_names = [
    "bash_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "glob_search_tool",
    "grep_search_tool",
    "inspect_tool",
]
agent_names = [
    "tool_evaluate_agent",
]
skill_names = [
    "tool_creator_skill",
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
tool_evaluate_agent.update(
    base_dir=workspace_root,
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)
