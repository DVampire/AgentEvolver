from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .agents.connector_evaluate_agent import connector_evaluate_agent
    from .memory.file_system_memory import file_system_memory

tag = "connector_evaluate_agent"
work_dir = f"work_dir/{tag}"
default_dir = f"work_dir/{tag}/default"
log_path = "connector_evaluate_agent.log"

use_local_proxy = True
model_name = "aws_claude/claude-opus-4.8"

tool_names = [
    "bash_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "glob_search_tool",
    "grep_search_tool",
    "inspect_connector",
]
agent_names = [
    "connector_evaluate_agent",
]
skill_names = [
    # unified connector-lifecycle connector; this agent reads its "Evaluating a connector" section.
    "connector_creator_skill",
]
connector_names = []
memory_names = [
    "file_system_memory",
]

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    require_grad=False,
)

#-----------------MEMORY CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    require_grad=False,
)

#-----------------AGENT CONFIG-----------------
connector_evaluate_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
