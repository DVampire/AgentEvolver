bash_tool = dict(
    enable_evolving = False,
    # Direct CLI/example runs are explicitly trusted host workflows. Gateway
    # sessions still refuse host execution and require a bound sandbox.
    #
    # One rule holds even here: a command may not write into the shared `extension/`
    # tree. That is not about trusting the host — promotion writes that tree and
    # records the version, the rollback backup and the registry entry as it does, so a
    # direct write leaves a component the registry does not know about. See
    # `agentevolver/permission/README.md`.
    permission_mode = "danger_full_access",
)
