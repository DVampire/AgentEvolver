from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.glob_search import glob_search_tool
    from .tools.grep_search import grep_search_tool
    from .agents.skill_evaluate_agent import skill_evaluate_agent
    from .memory.file_system_memory import file_system_memory

tag = "skill_evaluate_agent"
work_dir = f"work_dir/{tag}"
run_dir = f"work_dir/{tag}/run"
log_path = "skill_evaluate_agent.log"

model_name = "aws_claude/claude-opus-4.8"

tool_names = [
    "bash_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "glob_search_tool",
    "grep_search_tool",
    "inspect_skill",
]
agent_names = [
    "skill_evaluate_agent",
]
skill_names = [
    # unified skill-lifecycle skill; this agent reads its "Evaluating a skill" section.
    "skill_creator_skill",
]
connector_names = []
memory_names = [
    "file_system_memory",
]

#-----------------TOOL CONFIGS-----------------
bash_tool.update(
    enable_evolving=False,
)

#-----------------MEMORY CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    enable_evolving=False,
)

#-----------------AGENT CONFIG-----------------
skill_evaluate_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)
