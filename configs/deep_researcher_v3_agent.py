from mmengine.config import read_base
with read_base():
    from .tools.bash import bash_tool
    from .tools.todo import todo_tool
    from .agents.deep_researcher_v3 import deep_researcher_v3_agent

tag = "deep_researcher_v3_agent"
workdir = f"workdir/{tag}"
log_path = "agent.log"

use_local_proxy = True
version = "0.1.0"
model_name = "openrouter/gemini-3.1-pro-preview"

memory_names = []
tool_names = [
    'bash_tool',
    'todo_tool',
    'done_tool',
]
skill_names = []
agent_names = [
    "deep_researcher_v3_agent",
]

#-----------------BASH TOOL CONFIG-----------------
bash_tool.update(
    require_grad=False,
)
#-----------------TODO TOOL CONFIG-----------------
todo_tool.update(
    base_dir="tool/todo",
    require_grad=False,
)

#-----------------DEEP RESEARCHER V3 AGENT CONFIG-----------------
deep_researcher_v3_agent.update(
    workdir=workdir,
    model_name=model_name,
    max_steps=20,
    require_grad=False,
    use_memory=False,
)
