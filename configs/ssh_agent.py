from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.ssh_agent import ssh_agent
    from .tools.bash import bash_tool
    from .memory.file_system_memory import file_system_memory

tag = "ssh_agent"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
log_path = "agent.log"

version = "0.1.0"
model_name = "llm_hub/claude-opus-5"

memory_names = [
    "file_system_memory",
]
agent_names = [
    "ssh_agent",
]
# The local tools stay. The usual shape of remote work is prepare-here-run-there, and
# `env__upload` needs something to have produced the file it uploads. What is deliberately
# absent is any local tool that could be mistaken for a remote one — the split between
# `bash_tool` (here) and `env__run` (there) is the agent's whole mental model, and a second
# way to reach a shell would blur it.
tool_names = [
    'bash_tool',
    'done_tool',
]
skill_names = []
connector_names = []
env_names = [
    "remote_host",
]

#-----------------REMOTE HOST CONFIG-----------------
# The machine the agent operates. `host` is the only required field; everything else has a
# working default. Point it at a host from your `~/.ssh/config` and the alias is enough —
# user, port, identity and jump host all come from there.
#
# Authentication is by key only, resolved by ssh itself. Nothing here takes a password:
# a password would have to live in a config file, and a config file is the one place a
# credential must never be.
remote_host = dict(
    host="",                     # hostname or ~/.ssh/config alias — REQUIRED
    user="",                     # blank → whatever ssh resolves for the host
    port=22,
    identity_file="",            # blank → ssh's own key resolution
    jump_host="",                # e.g. "bastion.example.com" for a two-hop reach
    # Everything the agent does is confined below this. Point it at a project directory
    # rather than the home directory: the boundary is only as useful as it is narrow.
    workspace_root="~",
    connect_timeout=15,
    # Host key checking, on. Turning it off would make the agent connect to whatever
    # answers on that address — which is the entire attack it is meant to prevent.
    known_hosts_strict=True,
    # Whether the agent may start work that outlives the conversation. Left on because a
    # remote machine is usually wanted for exactly that; turn it off for a host where
    # nothing should keep running after the task ends.
    allow_launch=True,
    max_upload_mb=500,
    # A read-only terminal onto the agent's session, tunnelled to the frontend. The remote
    # server listens on loopback only and reaches the browser through the ssh connection
    # that is already open.
    live_view=True,
    state_entries=20,
)

#-----------------BASH TOOL CONFIG-----------------
bash_tool.update(
    enable_evolving=False,
)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    enable_evolving=False,
)

#-----------------SSH AGENT CONFIG-----------------
ssh_agent.update(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
)
