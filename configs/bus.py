"""Bus config — OLD search mode (Gemini plugins, single-round LLM search).

Usage:
    python examples/run_hle.py --config configs/bus.py
"""
from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.planning import planning_agent
    from .agents.deep_researcher_light import deep_researcher_light_agent
    from .agents.deep_analyzer_light import deep_analyzer_light_agent
    from .agents.opencode import opencode_agent

    from .tools.bash import bash_tool
    from .tools.todo import todo_tool
    from .tools.skill_generator import skill_generator_tool
    from .memory.general_memory_system import memory_system as general_memory_system
    from .memory.optimizer_memory_system import memory_system as optimizer_memory_system
    from .benchmarks.hle import hle_benchmark

tag = "bus"
workdir = f"workdir/{tag}"
log_path = "bus.log"
max_tokens = 8192

use_local_proxy = True
version = "0.1.0"
model_name = "openrouter/gemini-3.1-pro-preview"

# Tools available to sub-agents
tool_names = [
    "bash_tool",
    "python_interpreter_tool",
    "done_tool",
    "todo_tool"
]
memory_names = [
    "general_memory_system",
    "optimizer_memory_system",
]
# Agents on the bus: planner + sub-agents
agent_names = [
    "planning_agent",
    "deep_researcher_light_agent",
    "deep_analyzer_light_agent",
    "opencode_agent",
]
skill_names = [
    "hello_world_skill",
]

# -----------------TOOL CONFIG-----------------
bash_tool.update(require_grad=False)
todo_tool.update(
    base_dir="tool/todo_tool",
    require_grad=False,
)
skill_generator_tool.update(model_name=model_name, base_dir="skill")

# -----------------MEMORY CONFIG-----------------
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

# -----------------AGENT CONFIG-----------------
planning_agent.update(
    workdir=f"{workdir}/agent/planning_agent",
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    max_rounds=20,
)
deep_researcher_light_agent.update(
    workdir=f"{workdir}/agent/deep_researcher_light_agent",
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_llm_search=True,
    search_llm_models=[
        "openrouter/gemini-3-flash-preview-plugins",
    ],
)
deep_analyzer_light_agent.update(
    workdir=f"{workdir}/agent/deep_analyzer_light_agent",
    model_name="openrouter/gemini-3.1-pro-preview",
    memory_name=memory_names[0],
    require_grad=False,
    analyzer_llm_models=[
        "newapi/gemini-3.1-pro-preview",
        "openai/gpt-5.4",
    ],
)
opencode_agent.update(
    workdir=f"{workdir}/agent/opencode_agent",
    model_name="openrouter/anthropic/claude-opus-4.6",
    summary_model="newapi/gemini-3.1-pro-preview",
    memory_name=memory_names[0],
    require_grad=False,
)
