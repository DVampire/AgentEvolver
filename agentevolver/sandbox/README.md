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

Host Bash, persistent Terminal, and HostSandbox commands require Linux bubblewrap.
Their PID namespace contains descendants even after `setsid` or a double fork; namespace
teardown ends those descendants. Missing namespace support fails explicitly: use a
container backend instead. This ownership boundary preserves the host filesystem/network
policy and is not a security sandbox for arbitrary untrusted code. Custom tools that
create subprocesses directly must also use an owned backend.

The container ledger records PID plus creation time, avoiding PID-reuse confusion.
Only provably dead owners are reaped; unknown legacy owners require explicit cleanup.
