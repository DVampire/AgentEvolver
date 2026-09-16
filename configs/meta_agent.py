from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.meta_agent import meta_agent
    from .agents.code_agent import code_agent
    from .tools.bash import bash_tool
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
# One worker. Every registered agent reaches the model as a tool schema, and a roster
# of eight cost more prompt than the difference between them was worth on ordinary work:
# `code_agent` and `general_agent` differ by a prompt, and the reviewer, monitor and the
# three evolution roles are for runs that ask for them. Add one back per run that needs it.
agent_names = [
    "meta_agent",
    "code_agent",
]
# What rides on every step as a schema — not what the run is capable of. Anything not
# here is still reachable: `inspect_tool` reads any registered capability by name.
#
# One consequence worth stating: `exit_plan_mode` is gone, and it is plan mode's only
# legal move. Run with `--plan-mode plan` and add it back, or the gate refuses everything
# and the run has nowhere to go.
tool_names = [
    # A shell and a way to stop. Everything the file tools did, a shell does — read with
    # `cat`, write with a heredoc, look around with `ls` — and each schema that rides on
    # every step is paid for on every step. Measured on a three-line task: twenty-four
    # schemas were 22k tokens of prompt against under 1k of task.
    "bash_tool",
    "done_tool",
    # A blocked child is answered on the ordinary turn its question arrives, with
    # `reply_tool`. Both halves of that channel are off by default and belong together:
    # add `escalate_tool` for children and `reply_tool` here, or neither.
    # "escalate_tool",
    # "reply_tool",
]
# Nothing resident. A skill is a method the agent reads when it needs one, and a run
# that needs `self_evolving_skill` names it.
skill_names = [
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


#-----------------META AGENT CONFIG-----------------
meta_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    max_step = 50,
)
