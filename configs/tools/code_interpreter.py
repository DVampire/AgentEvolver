# Code Interpreter Tool Configuration
code_interpreter_tool = dict(
    enable_evolving = False,
    # Kernel mode is the default: state carries across calls and figures come back
    # as images. Turn it off for a run whose files live in a peer sandbox — the
    # kernel starts in the base environment and cannot see them, so it answers
    # FileNotFoundError to code that is looking at real files.
    use_kernel = True,
)
