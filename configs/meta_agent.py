from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .agents.general_agent import general_agent
    from .agents.reviewer_agent import reviewer_agent
    from .agents.monitor_agent import monitor_agent
    from .agents.browser_agent import browser_agent
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
    from .agents.memory_generate_agent import memory_generate_agent
    from .agents.memory_optimize_agent import memory_optimize_agent
    from .agents.memory_evaluate_agent import memory_evaluate_agent
    from .agents.connector_generate_agent import connector_generate_agent
    from .agents.connector_optimize_agent import connector_optimize_agent
    from .agents.connector_evaluate_agent import connector_evaluate_agent
    from .tools.bash import bash_tool
    from .tools.read_image import read_image_tool
    from .tools.ask_user import ask_user_question
    from .tools.exit_plan_mode import exit_plan_mode
    from .tools.get_goal import get_goal_tool
    from .tools.create_goal import create_goal_tool
    from .tools.update_goal import update_goal_tool
    from .tools.schedule_create import schedule_create_tool
    from .tools.session_search import session_search_tool
    from .tools.session_event_search import session_event_search_tool
    from .tools.session_read import session_read_tool
    from .tools.session_event_read import session_event_read_tool
    from .tools.session_trace import session_trace_tool
    from .tools.job_list import job_list_tool
    from .tools.job_output import job_output_tool
    from .tools.job_kill import job_kill_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.git import git_tool
    from .tools.deploy import deploy_tool
    from .tools.evolution import evolution_tool
    from .tools.escalate import escalate_tool
    from .memory.file_system_memory import file_system_memory

tag = "meta_agent"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
project_root = "output/local"
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
    # browser agent — drives a real browser to VERIFY web/UI deliverables hands-on
    # (render, click, check images/console) via the browser_environment.
    "browser_agent",
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
    "memory_generate_agent",
    "memory_optimize_agent",
    "memory_evaluate_agent",
    "connector_generate_agent",
    "connector_optimize_agent",
    "connector_evaluate_agent",
]
tool_names = [
    "bash_tool",
    "read_image_tool",
    "ask_user_question",
    "exit_plan_mode",
    "get_goal_tool",
    "create_goal_tool",
    "update_goal_tool",
    "schedule_create_tool",
    "session_search_tool",
    "session_event_search_tool",
    "session_read_tool",
    "session_event_read_tool",
    "session_trace_tool",
    # background work — start something long with bash_tool(run_in_background),
    # then keep working and collect it instead of spending a step waiting
    "job_list_tool",
    "job_output_tool",
    "job_kill_tool",
    "done_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "git_tool",
    "deploy_tool",
    "evolution_tool",
    "escalate_tool",
    "reply_tool",
    "report_tool",
    "send_message_tool",
    # web retrieval — search the web, fetch a page, and download REAL images to bundle
    "web_searcher_tool",
    "web_fetcher_tool",
    "media_search_tool",
]
# Resident rosters are what reaches the model on every step, as a tool schema each. The
# rest of the registry is not gone: `inspect_capability_tool` reads any registered
# capability by name, so a run that needs one can look it up. Keeping the resident set
# small is what leaves room for the conversation — 21 connectors alone expanded to 213
# action schemas and 48k tokens, against a 95k input capacity.
skill_names = [
    # Kept resident because a prompt or another config names them: the creator triad
    # skills and `self_evolving_skill` are what this configuration exists to run.
    "agent_creator_skill",
    "tool_creator_skill",
    "skill_creator_skill",
    "connector_creator_skill",
    "environment_creator_skill",
    "memory_creator_skill",
    "self_evolving_skill",
    # The everyday four.
    "code_review_skill",
    "verify_skill",
    "run_skill",
    "deep_research_skill",
    # Deliverable craft, named by the report and artifact prompts.
    "report_design_skill",
    "artifact_design_skill",
]
connector_names = [
    "chemistry_connector",
    "literature_graph_connector",
]

#-----------------ENVIRONMENT CONFIGS-----------------
# browser_agent verifies web/UI deliverables in a real browser. Under Model X the
# agent runs in the base container; the browser runs as a separate peer container
# (opensandbox/chrome) spawned via the Docker socket and driven over CDP — so the
# base image needs no chromium of its own. use_sandbox=True selects that peer.
env_names = [
    "browser_environment",
    "computer_environment",
    "remote_host",
]

# ---------------- COMPUTER (DESKTOP) ----------------
# A whole Linux desktop the agent drives with mouse and keyboard. The container is
# started on first use, not at boot, so listing it here costs nothing until something
# actually opens it.
computer_environment = dict(
    width=1920,
    height=1080,
    use_som=True,
    sandbox_timeout_minutes=60,
)

# ---------------- REMOTE HOSTS (SSH) ----------------
# Ships with no machines. A remote agent must never connect somewhere nobody named, and
# an example host in a shipped config is exactly how that happens. Add machines from the
# frontend's "Remote machines" panel — they persist to output/.runtime/ssh_hosts.json —
# or seed them here for a deployment that always has the same ones.
remote_host = dict(
    hosts=[],
    allow_launch=True,
    max_upload_mb=500,
    live_view=True,
    state_entries=20,
)
browser_environment = dict(
    base_dir="environment/browser",
    headless=True,          # ignored when vnc=True (chrome-vnc forces headful)
    viewport=dict(width=1280, height=900),
    use_sandbox=True,
    vnc=True,               # use the chrome-vnc sandbox (headful Chrome + live noVNC view)
    use_som=True,
    state_detail="elements",
    max_state_elements=0,
    command_timeout=30.0,
)

#-----------------TOOL CONFIGS-----------------
bash_tool.update(enable_evolving=False)
git_tool.update(timeout=60)

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

browser_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    env_name="browser_environment",
    enable_evolving=False,
    use_memory=True,
)

#-----------------OPTIMIZER AGENT CONFIGS-----------------
tool_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

#-----------------GENERATOR AGENT CONFIGS-----------------
tool_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

#-----------------EVALUATOR AGENT CONFIGS-----------------
tool_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

#-----------------FULL TRIAD CONFIGS (agent/skill/environment/connector)-----------------
agent_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

agent_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

agent_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

skill_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

skill_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

skill_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

environment_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

environment_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

environment_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

connector_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

connector_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

connector_evaluate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)


#-----------------MEMORY TRIAD CONFIGS-----------------
memory_generate_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

memory_optimize_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)

memory_evaluate_agent.update(
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
