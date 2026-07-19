---
name: permission
description: "Evaluates command and filesystem operations against explicit permission modes and policies. It also applies size and binary-file safeguards."
version: 0.1.0
type: module
category: permission
requirements: []
metadata:
  tracks_package_version: true
---
# Permission

Evaluates command and filesystem operations against explicit permission modes and policies.
It also applies size and binary-file safeguards.

| File | Responsibility |
|---|---|
| `types.py` | Modes, operations, rules, validation, and helper checks |
| `context.py` | Registered entity policies |
| `server.py` | Public `permission_manager` enforcement facade |

Permission authorizes a proposed operation; Tool and Sandbox remain responsible for
executing it safely.
