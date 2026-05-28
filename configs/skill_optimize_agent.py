from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .agents.skill_optimize_agent import skill_optimize_agent

tag = "skill_optimize_agent"
work_dir = f"work_dir/{tag}"
log_path = "skill_optimize_agent.log"

use_local_proxy = True
model_name = "openrouter/gemini-3-flash-preview"

tool_names = [
    "bash_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "glob_search_tool",
    "grep_search_tool",
]
agent_names = [
    "skill_optimize_agent",
]
skill_names = []
memory_names = [
    "file_system_memory",
]

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    require_grad=False,
)

#-----------------AGENT CONFIG-----------------
skill_optimize_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=False,
)
