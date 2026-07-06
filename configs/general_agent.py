from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.general_agent import general_agent
    from .tools.mdify import mdify_tool
    from .tools.bash import bash_tool
    from .memory.file_system_memory import file_system_memory

tag = "general_agent"
work_dir = f"work_dir/{tag}"
default_dir = f"work_dir/{tag}/default"
log_path = "agent.log"

use_local_proxy = True
version = "0.1.0"
model_name = "aws_claude/claude-opus-4.8"

memory_names = [
    "file_system_memory",
]
agent_names = [
    "general_agent"
]
tool_names = [
    'bash_tool',
    'code_interpreter_tool',
    'done_tool',
    'inspect_tool',
    'todo_tool',
]
skill_names = [
    "hello_world_skill",
]
connector_names = [
    "biorxiv_connector",
    "chembl_connector",
    "clinical_trials_connector",
    "pubmed_connector",
]

#-----------------BASH TOOL CONFIG-----------------
bash_tool.update(
    require_grad=False,
)
#-----------------MDIFY TOOL CONFIG-----------------
mdify_tool.update(
    base_dir="tool/mdify",
)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    require_grad=False,
)

#-----------------REASON ACT AGENT CONFIG-----------------
general_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
