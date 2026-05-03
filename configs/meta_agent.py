from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .agents.reason_act_agent import reason_act_agent
    from .agents.tool_optimize_agent import tool_optimize_agent
    from .agents.tool_evaluate_agent import tool_evaluate_agent
    from .agents.tool_generate_agent import tool_generate_agent
    from .tools.bash import bash_tool
    from .tools.todo import todo_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.git import git_tool
    from .memory.general_memory_system import memory_system as general_memory_system

tag = "meta_agent"
workdir = f"workdir/{tag}"
log_path = "agent.log"

use_local_proxy = True
model_name = "openrouter/gemini-3-flash-preview"

memory_names = [
    "general_memory_system",
]
agent_names = [
    "meta_agent",
    "code_agent",
    "reason_act_agent",
    "tool_optimize_agent",
    "tool_evaluate_agent",
    "tool_generate_agent",
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
]
skill_names = []

#-----------------TOOL CONFIGS-----------------
bash_tool.update(require_grad=False)
todo_tool.update(base_dir="tool/todo", require_grad=False)
git_tool.update(timeout=60)

#-----------------MEMORY SYSTEM CONFIG-----------------
general_memory_system.update(
    base_dir="memory/general_memory_system",
    model_name=model_name,
    max_summaries=10,
    max_insights=10,
    require_grad=False,
)

#-----------------ACTOR AGENT CONFIGS-----------------
code_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

reason_act_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------OPTIMIZER AGENT CONFIGS-----------------
tool_optimize_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------GENERATOR AGENT CONFIGS-----------------
tool_generate_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------EVALUATOR AGENT CONFIGS-----------------
tool_evaluate_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------META AGENT CONFIG-----------------
meta_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
