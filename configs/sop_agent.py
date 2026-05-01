from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.sop import sop_agent
    from .tools.bash import bash_tool
    from .tools.todo import todo_tool
    from .memory.general_memory_system import memory_system as general_memory_system

tag = "sop_agent"
workdir = f"workdir/{tag}"
log_path = "agent.log"

use_local_proxy = True
version = "0.1.0"
model_name = "int_openrouter/gemini-3.1-pro-preview"

memory_names = [
    "general_memory_system",
]
agent_names = [
    "sop_agent",
]
tool_names = [
    'bash_tool',
    "python_interpreter_tool",
    'done_tool',
    'todo_tool',
]
skill_names = [
    "markov_hitting_time_skill",
    "discrete_linear_system_skill",
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
    "diptera_appendage_counter_skill",
    "protein_engineering_skill",
    "cancer_genomics_scoring_skill",
    "pharmacy_counseling_skill",
    "pharmacy_compounding_skill",
    "rnaseq_filtering_skill",
    "pediatric_clinical_calculation_skill",
    "environmental_risk_assessment_skill",
    "dna_translation_skill"
]

#-----------------BASH TOOL CONFIG-----------------
bash_tool.update(
    require_grad=False,
    timeout=60,
)
#-----------------TODO TOOL CONFIG-----------------
todo_tool.update(
    base_dir="tool/todo",
    require_grad=False,
)
#-----------------MEMORY SYSTEM CONFIG-----------------
general_memory_system.update(
    base_dir="memory/general_memory_system",
    model_name=model_name,
    max_summaries=10,
    max_insights=10,
    require_grad=False,
)
#-----------------SOP AGENT CONFIG-----------------
sop_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
