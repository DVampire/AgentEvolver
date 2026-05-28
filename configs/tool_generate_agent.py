from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .agents.tool_generate_agent import tool_generate_agent

tag = "tool_generate_agent"
work_dir = f"work_dir/{tag}"
default_dir = f"work_dir/{tag}/default"
log_path = "tool_generate_agent.log"

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
    "tool_generate_agent",
]
skill_names = []
memory_names = []

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    require_grad=False,
)

#-----------------AGENT CONFIG-----------------
tool_generate_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    require_grad=False,
    use_memory=False,
)
