from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.code_agent import code_agent
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.git import git_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .memory.file_system_memory import file_system_memory

tag = "code_agent"
work_dir = f"work_dir/{tag}"
default_dir = f"work_dir/{tag}/default"
log_path = "agent.log"

use_local_proxy = True
model_name = "aws_claude/claude-opus-4.8"

memory_names = [
    "file_system_memory",
]
agent_names = [
    "code_agent",
]
tool_names = [
    "bash_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
    "glob_search_tool",
    "grep_search_tool",
    "inspect_tool",
    "todo_tool",
]
skill_names = []
connector_names = [
    "biorxiv_connector",
    "chembl_connector",
    "clinical_trials_connector",
    "pubmed_connector",
]

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    require_grad=False,
)
git_tool.update(
    timeout=60,
)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
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
