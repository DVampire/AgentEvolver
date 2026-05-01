opencode_agent = dict(
    workdir="workdir/opencode_agent",
    name="opencode_agent",
    type="Agent",
    description=(
        "Coding agent powered by the opencode CLI. Supports Python and R for "
        "data analysis, computation, and scripting tasks. Runs `opencode run \"<task>\"` "
        "inside a session-scoped working directory and returns the full execution output."
    ),
    model_name="openrouter/claude-opus-4.6",
    summary_model_name="openrouter/gemini-3-flash-preview",
    require_grad=False,
    max_iterations=30,
)
