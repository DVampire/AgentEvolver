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
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.git import git_tool
    from .memory.file_system_memory import file_system_memory

tag = "meta_agent"
work_dir = f"work_dir/{tag}"
default_dir = f"work_dir/{tag}/default"
extension_dir = f"work_dir/{tag}/extension"
log_path = "agent.log"

use_local_proxy = True
model_name = "int_openrouter/gemini-3.1-pro-preview"

memory_names = [
    "file_system_memory",
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
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
    "tool_eval_runner",
]
skill_names = [
    # worker skills — sub-agents (code_agent / reason_act_agent) see these; the
    # MetaAgent sees none (it orchestrates from its prompt + the agent registry).
    "code_review",
    "security_review",
    "deep_research",
    "simplify",
    "review",
    "verify",
    "run",
    "init",
    # generation skill for the tool_generate_agent sub-agent (in agent_names below)
    "generate_tool_skill",
]

#-----------------TOOL CONFIGS-----------------
bash_tool.update(require_grad=False)
git_tool.update(timeout=60)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    require_grad=False,
)

#-----------------ACTOR AGENT CONFIGS-----------------
code_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

reason_act_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------OPTIMIZER AGENT CONFIGS-----------------
tool_optimize_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------GENERATOR AGENT CONFIGS-----------------
tool_generate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------EVALUATOR AGENT CONFIGS-----------------
tool_evaluate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

#-----------------META AGENT CONFIG-----------------
meta_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
