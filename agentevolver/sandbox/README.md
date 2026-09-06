---
name: sandbox
description: "Defines isolated command execution, managed sandbox processes, and staged project validation."
version: 1.0.0
type: module
category: sandbox
requirements: []
metadata: {}
---
# Sandbox

Defines isolated command execution, managed sandbox processes, and staged project validation.

| File | Responsibility |
|---|---|
| `types.py` | Sandbox configuration and execution results |
| `server.py` | Public `sandbox_manager` facade |
| `process.py` | Managed sandbox-server processes and owned host-command PID namespaces |
| `project.py` | Project staging and validation helpers |
| `default/` | Built-in sandbox backends |

Permission decides whether an operation is allowed; Sandbox provides the execution boundary.

The base launcher mounts the live checkout read-write at `/workspace/AgentEvolver`
and uses it as the working directory. It publishes `AGENTEVOLVER_HOST_ROOT` and
`AGENTEVOLVER_CONTAINER_ROOT`; both Docker and OpenSandbox translate peer mount
sources back to the host namespace before asking the host daemon to mount them.

`ProjectSandbox.mounts()` places the task workspace at `/workspace` and its resources
under `/workspace/.agentevolver/`: `package`, `extension` (session staging),
`extension-base` (shared library), and `log`. All are writable, including framework
source and shared extensions. File tools and Bash follow this policy; explicit
`read_only` execution modes and plan approval gates still apply. Promotion remains
the operation that records candidate validation, adoption, and rollback history.

IDE peers place their persistent editor data, live framework checkout, and shared
extensions under `/workspace/.agentevolver/` as well. Custom `SandboxConfig.mounts`
and image-specific work directories remain explicit caller choices; mount paths are
not silently rewritten. Official benchmark grading retains its prescribed paths.

Host Bash, persistent Terminal, and HostSandbox commands require Linux bubblewrap.
Their PID namespace contains descendants even after `setsid` or a double fork; namespace
teardown ends those descendants. Missing namespace support fails explicitly: use a
container backend instead. This ownership boundary preserves the host filesystem/network
policy and is not a security sandbox for arbitrary untrusted code. Custom tools that
create subprocesses directly must also use an owned backend.

The container ledger records PID plus creation time, avoiding PID-reuse confusion.
Only provably dead owners are reaped; unknown legacy owners require explicit cleanup.
