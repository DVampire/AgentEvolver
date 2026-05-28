from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.reason_act_agent import reason_act_agent
    from .tools.mdify import mdify_tool
    from .tools.bash import bash_tool
    from .memory.general_memory_system import memory_system as general_memory_system

tag = "reason_act_agent"
work_dir = f"work_dir/{tag}"
default_dir = f"work_dir/{tag}/default"
log_path = "agent.log"

use_local_proxy = True
version = "0.1.0"
model_name = "openrouter/gemini-3-flash-preview"

memory_names = [
    "general_memory_system",
    "optimizer_memory_system"
]
agent_names = [
    "reason_act_agent"
]
tool_names = [
    'bash_tool',
    'python_interpreter_tool',
    'done_tool',
]
skill_names = [
    "hello_world_skill",
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
general_memory_system.update(
    base_dir="memory/general_memory_system",
    model_name=model_name,
    max_summaries=10,
    max_insights=10,
    require_grad=False,
)

#-----------------REASON ACT AGENT CONFIG-----------------
reason_act_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
