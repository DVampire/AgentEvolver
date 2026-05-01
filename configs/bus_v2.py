"""Bus config — V2 search mode (Serper + Jina, multi-round web search).

Usage:
    python examples/run_hle.py --config configs/bus_v2.py
"""
from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.planning import planning_agent
    from .agents.deep_researcher_v2 import deep_researcher_v2_agent
    from .agents.deep_analyzer_v2 import deep_analyzer_v2_agent
    from .agents.opencode import opencode_agent
    from .agents.tool_calling import tool_calling_agent
    from .agents.sop import sop_agent

    from .tools.bash import bash_tool
    from .tools.todo import todo_tool
    from .memory.general_memory_system import memory_system as general_memory_system
    from .memory.optimizer_memory_system import memory_system as optimizer_memory_system
    from .benchmarks.hle import hle_benchmark

tag = "bus_v2"
workdir = f"workdir/{tag}"
log_path = "bus_v2.log"
max_tokens = 32768

use_local_proxy = True
version = "0.1.0"
model_name = "int_openrouter/gemini-3.1-pro-preview"

# Tools available to sub-agents
tool_names = [
    "bash_tool",
    "done_tool",
    'todo_tool',
]
memory_names = [
    "general_memory_system",
    "optimizer_memory_system",
]
# Agents on the bus: planner + sub-agents
agent_names = [
    "planning_agent",
    "deep_researcher_v2_agent",
    "deep_analyzer_v2_agent",
    "opencode_agent",
    # "tool_calling_agent",
    "sop_agent",
]
skill_names = [
    "wiki_search_skill",
    # SOP skills — loaded by sop_agent at runtime. Each targets a distinct
    # problem class; the SOP agent picks the matching one per task.
    "abstract_algebra_topology_skill",
    "bio_med_experimental_design_skill",
    "cs_algorithm_complexity_skill",
    "differential_equation_integration_skill",
    "factual_knowledge_lookup_skill",
    "image_grounded_expert_reasoning_skill",
    "materials_property_prediction_skill",
    "novel_spec_simulation_skill",
    "theoretical_physics_modeling_skill",
    "variational_nonlinear_pde_skill",
]

# -----------------TOOL CONFIG-----------------
bash_tool.update(require_grad=False)
#-----------------TODO TOOL CONFIG-----------------
todo_tool.update(
    base_dir="tool/todo",
    require_grad=False,
)

# -----------------MEMORY CONFIG-----------------
general_memory_system.update(
    base_dir="memory/general_memory_system",
    model_name = "int_openrouter/gemini-3-flash-preview",
    max_summaries=10,
    max_insights=10,
    require_grad=False,
)
optimizer_memory_system.update(
    base_dir="memory/optimizer_memory_system",
    model_name = "int_openrouter/gemini-3-flash-preview",
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
deep_researcher_v2_agent.update(
    workdir=f"{workdir}/agent/deep_researcher_v2_agent",
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    max_rounds=3,
    num_results=10,
    summary_model_name="int_openrouter/grok-4.1-fast",
    llm_search_models=[
        "openrouter/gemini-3.1-pro-preview-plugins", # official search model with plugins support
    ],
    fetch_timeout=20.0
)
deep_analyzer_v2_agent.update(
    workdir=f"{workdir}/agent/deep_analyzer_v2_agent",
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    max_rounds=3,
    summary_model_name="int_openrouter/grok-4.1-fast",
    general_analyze_models = [
        "openrouter/gemini-3.1-pro-preview-plugins", # can analyze audio, video, image, pdf, etc.
    ],
    llm_analyze_models = [
        "int_openrouter/gpt-5.4", # can analyze complex text with image
    ],
    advanced_analyze_models = [
        "int_openrouter/gpt-5.4-pro", # expensive but powerful model for deep analysis
    ],
)
opencode_agent.update(
    workdir=f"{workdir}/agent/opencode_agent",
    model_name="int_openrouter/claude-opus-4.6",
    summary_model_name="int_openrouter/grok-4.1-fast",
    memory_name=memory_names[0],
    require_grad=False,
)
# tool_calling_agent.update(
#     workdir=f"{workdir}/agent/tool_calling_agent",
#     description = "A tool-calling agent equipped with wiki_search_skill for looking up encyclopedic definitions and explanations of specialised terminology in biology, physics, and mathematics.",
#     model_name="int_openrouter/gemini-3-flash-preview",
#     memory_name=memory_names[0],
#     require_grad=False,
#     use_memory=True,
# )
sop_agent.update(
    workdir=f"{workdir}/agent/sop_agent",
    description=(
        "Runs a phase-by-phase SOP skill for: abstract algebra / topology, "
        "theoretical physics, variational & nonlinear PDEs, ODEs & integration, "
        "CS algorithms & complexity, materials properties, bio/med experimental "
        "design, humanities attribution lookup, novel-spec simulation, or "
        "image-grounded expert reasoning. Prefer over an analyzer agent "
        "whenever the task touches any of these domains, even partially."
    ),
    model_name="int_openrouter/gpt-5.3-codex",
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
    max_steps=50,
)
