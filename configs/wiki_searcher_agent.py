from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.wiki_searcher import wiki_searcher_agent
    from .tools.mdify import mdify_tool
    from .tools.bash import bash_tool
    from .tools.todo import todo_tool
    from .tools.skill_generator import skill_generator_tool
    from .memory.general_memory_system import memory_system as general_memory_system
    from .memory.optimizer_memory_system import memory_system as optimizer_memory_system

tag = "wiki_searcher_agent"
workdir = f"workdir/{tag}"
log_path = "agent.log"

use_local_proxy = True
version = "0.1.0"
model_name = "newapi/gemini-3.1-pro-preview"

memory_names = [
    "general_memory_system",
    "optimizer_memory_system"
]
agent_names = [
    "wiki_searcher_agent"
]
tool_names = [
    'bash_tool',
    'done_tool',
    'todo_tool',
]
skill_names = [
    "wiki_search_skill",
]

#-----------------BASH TOOL CONFIG-----------------
bash_tool.update(
    require_grad=False,
)
#-----------------MDIFY TOOL CONFIG-----------------
mdify_tool.update(
    base_dir="tool/mdify",
)
#-----------------TODO TOOL CONFIG-----------------
todo_tool.update(
    base_dir="tool/todo",
    require_grad=False,
)
#-----------------SKILL GENERATOR TOOL CONFIG-----------------
skill_generator_tool.update(
    model_name="newapi/gemini-3.1-pro-preview",
    base_dir="skill",
)
#-----------------MEMORY SYSTEM CONFIG-----------------
general_memory_system.update(
    base_dir="memory/general_memory_system",
    model_name=model_name,
    max_summaries=10,
    max_insights=10,
    require_grad=False,
)
optimizer_memory_system.update(
    base_dir="memory/optimizer_memory_system",
    model_name=model_name,
    max_records_per_session=10,
    require_grad=False,
)

#-----------------WIKI SEARCHER AGENT CONFIG-----------------
wiki_searcher_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=False,
)
