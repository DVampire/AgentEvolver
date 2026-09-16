ssh_agent = dict(
    name = "ssh_agent",
    type = "SSHAgent",
    description = "An agent that operates a remote machine over SSH: running commands, managing long-running jobs, editing files and moving data between the two machines.",
    model_name = "llm_hub/claude-opus-5",
    prompt_name = "ssh_agent",
    memory_name = "file_system_memory",
    env_name = "remote_host",
    # Lower than a local agent's. Every remote action is a network round trip, and a wide
    # batch is a wide blast radius on a machine that is not ours — the agent should see
    # what each change did before stacking the next one on top of it.
    max_actions = 5,
    # Higher than a local agent's, for the opposite reason: work on a remote host arrives
    # in more, smaller steps — launch, check the log, check it again — and running out of
    # steps mid-job leaves something running with nobody watching it.
    max_step = 40,
    max_token = 100_000_000,
    timeout = 3600,
    review_steps = 5,
    log_max_length = 1000,
    enable_evolving = False,
    use_memory = True,
)
