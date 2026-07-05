from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .agents.general_agent import general_agent
    from .agents.tool_optimize_agent import tool_optimize_agent
    from .agents.tool_evaluate_agent import tool_evaluate_agent
    from .agents.tool_generate_agent import tool_generate_agent
    from .agents.agent_generate_agent import agent_generate_agent
    from .agents.agent_optimize_agent import agent_optimize_agent
    from .agents.agent_evaluate_agent import agent_evaluate_agent
    from .agents.skill_generate_agent import skill_generate_agent
    from .agents.skill_optimize_agent import skill_optimize_agent
    from .agents.skill_evaluate_agent import skill_evaluate_agent
    from .agents.environment_generate_agent import environment_generate_agent
    from .agents.environment_optimize_agent import environment_optimize_agent
    from .agents.environment_evaluate_agent import environment_evaluate_agent
    from .agents.connector_generate_agent import connector_generate_agent
    from .agents.connector_optimize_agent import connector_optimize_agent
    from .agents.connector_evaluate_agent import connector_evaluate_agent
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
model_name = "aws_claude/claude-opus-4.8"

memory_names = [
    "file_system_memory",
]
agent_names = [
    "meta_agent",
    "code_agent",
    "general_agent",
    "tool_optimize_agent",
    "tool_evaluate_agent",
    "tool_generate_agent",
    "agent_generate_agent",
    "agent_optimize_agent",
    "agent_evaluate_agent",
    "skill_generate_agent",
    "skill_optimize_agent",
    "skill_evaluate_agent",
    "environment_generate_agent",
    "environment_optimize_agent",
    "environment_evaluate_agent",
    "connector_generate_agent",
    "connector_optimize_agent",
    "connector_evaluate_agent",
]
tool_names = [
    "bash_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
]
skill_names = [
    # worker skills — the skill pool for this session's sub-agents (code/general/triads).
    "code_review_skill",
    "security_review_skill",
    "deep_research_skill",
    "simplify_skill",
    "review_skill",
    "verify_skill",
    "run_skill",
    "init_skill",
    "report_design_skill",
    "artifact_design_skill",
    "theme_factory_skill",
    "doc_coauthoring_skill",
    # document / data I/O skills — deliver real Office & PDF artifacts
    "docx_skill",
    "xlsx_skill",
    "pdf_skill",
    "pptx_skill",
    # browser automation — test/verify local web apps, capture screenshots
    "webapp_testing_skill",
    # per-type creator skills (orchestrator role) — drive each triad's create->eval->improve loop
    "agent_creator_skill",
    "tool_creator_skill",
    "environment_creator_skill",
    # orchestrator skill: how to drive the skill create->evaluate->improve loop
    "skill_creator_skill",
    # orchestrator skill: how to drive the connector create->evaluate->improve loop
    "connector_creator_skill",
]
connector_names = []

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

general_agent.update(
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

#-----------------FULL TRIAD CONFIGS (agent/skill/environment/connector)-----------------
agent_generate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

agent_optimize_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

agent_evaluate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

skill_generate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

skill_optimize_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

skill_evaluate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

environment_generate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

environment_optimize_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

environment_evaluate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

connector_generate_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

connector_optimize_agent.update(
    base_dir=extension_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)

connector_evaluate_agent.update(
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
