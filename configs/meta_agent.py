from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .agents.general_agent import general_agent
    from .agents.reviewer_agent import reviewer_agent
    from .agents.monitor_agent import monitor_agent
    from .agents.generate_agent import generate_agent
    from .agents.optimize_agent import optimize_agent
    from .agents.evaluate_agent import evaluate_agent
    from .tools.bash import bash_tool
    from .tools.ask_user import ask_user_question
    from .tools.exit_plan_mode import exit_plan_mode
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.evolution import evolution_tool
    from .tools.escalate import escalate_tool
    from .memory.file_system_memory import file_system_memory

tag = "meta_agent"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
log_path = "agent.log"

model_name = "llm_hub/claude-opus-5"

memory_names = [
    "file_system_memory",
]
agent_names = [
    "meta_agent",
    "code_agent",
    "general_agent",
    "reviewer_agent",
    "monitor_agent",
    "generate_agent",
    "optimize_agent",
    "evaluate_agent",
]
# The basics, and nothing else. Anything not here is still reachable — `inspect_tool`
# reads any registered capability by name — so this is what rides on every step as a
# schema, not what the run is capable of.
tool_names = [
    # Read, write, look around, run something.
    "bash_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    # Talk to the person, and to whoever dispatched this run.
    "ask_user_question",
    "escalate_tool",
    "reply_tool",
    "report_tool",
    "done_tool",
    # Plan mode's only exit. The gate refuses everything with effects until a person
    # approves, so without this a run that enters plan mode has no legal move left.
    "exit_plan_mode",
    # MetaAgent's own reason to exist.
    "evolution_tool",
]
# Resident rosters are what reaches the model on every step, as a tool schema each. The
# rest of the registry is not gone: `inspect_tool` reads any registered
# capability by name, so a run that needs one can look it up. Keeping the resident set
# small is what leaves room for the conversation — 21 connectors alone expanded to 213
# action schemas and 48k tokens, against a 95k input capacity.
skill_names = [
    # The orchestrator's half of evolution: when to evolve, the gate, the loop. How to
    # write each of the eight component types is `generate_skill` / `optimize_skill` /
    # `evaluate_skill`, which the three agents load themselves — MetaAgent only dispatches.
    "self_evolving_skill",
    # # The everyday four.
    # "code_review_skill",
    # "verify_skill",
    # "run_skill",
    # "deep_research_skill",
    # # Deliverable craft, named by the report and artifact prompts.
    # "report_design_skill",
    # "artifact_design_skill",
]
# The baseline mounts one environment, and only because `bash_tool` cannot be mounted
# without it. `run_in_background` is an unconditional parameter of bash: with no job
# environment the agent gets a job id it can never read or kill — a capability that
# looks available and silently drops results, which is worse than not having it.
#
# It is close to free. `JobEnvironment.get_state` returns an empty string while nothing
# is outstanding, so an idle job environment costs three action schemas and no prompt
# at all. Everything else — browser, computer, remote_host — is added per run.
env_names = [
    "job",
]

# Absent means *all*, not none: `plugin_manager.initialize(None)` builds every
# registered plugin, and `agentevolver/plugins/` is 517 files. Stating the empty list
# is what actually turns them off.
plugin_names = []
connector_names = []
workflow_names = []


#-----------------TOOL CONFIGS-----------------
bash_tool.update(enable_evolving=False)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    # Evolvable: a long run can expose a retention defect in the memory system
    # itself, and the fix belongs to the memory resource rather than the agent.
    enable_evolving=True,
)

#-----------------ACTOR AGENT CONFIGS-----------------
code_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

general_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

reviewer_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

monitor_agent.update(
    enable_evolving=False,
)

#-----------------EVOLUTION AGENT CONFIGS-----------------
# One agent per role, each working on whichever of the eight component types it is
# dispatched with. There were eighteen of these blocks, identical but for the name.
generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)


#-----------------META AGENT CONFIG-----------------
meta_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    max_step = 50,
)
