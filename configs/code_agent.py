from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.code_agent import code_agent
    from .tools.bash import bash_tool
    from .tools.todo import todo_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.git import git_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .memory.general_memory_system import memory_system as general_memory_system

tag = "code_agent"
work_dir = f"work_dir/{tag}"
log_path = "agent.log"

use_local_proxy = True
model_name = "openrouter/gemini-3-flash-preview"

memory_names = [
    "general_memory_system",
]
agent_names = [
    "code_agent",
]
tool_names = [
    "bash_tool",
    "done_tool",
    "todo_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
    "glob_search_tool",
    "grep_search_tool",
]
skill_names = []

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    require_grad=False,
)
todo_tool.update(
    base_dir="tool/todo",
    require_grad=False,
)
git_tool.update(
    timeout=60,
)

#-----------------MEMORY SYSTEM CONFIG-----------------
general_memory_system.update(
    base_dir="memory/general_memory_system",
    model_name=model_name,
    max_summaries=10,
    max_insights=10,
    require_grad=False,
)

#-----------------CODE AGENT CONFIG-----------------
code_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
