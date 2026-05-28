from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.hello_world import hello_world_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .agents.tool_optimize_agent import tool_optimize_agent

tag = "tool_optimize_agent"
work_dir = f"work_dir/{tag}"
log_path = "tool_optimize_agent.log"

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
    "tool_optimize_agent",
]
skill_names = []
memory_names = [
    "general_memory_system",
]

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    require_grad=False,
)
#-----------------AGENT CONFIG-----------------
tool_optimize_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
