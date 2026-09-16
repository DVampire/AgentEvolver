---
name: tool_default
description: "Contains the framework's atomic tools for files, shell, git, search, inspection, evolution, deployment, task completion, and related operations. Each Tool exposes a narrow callable contract and is registered through the parent Tool Manager."
version: 1.0.0
type: collection
category: tool
requirements: []
metadata: {}
---
# Built-in tools

Contains the framework's atomic, model-facing adapters. Packages follow ownership rather
than growing one flat directory:

| Package | Responsibility |
|---|---|
| `workspace/` | Local files, patches, shell, Git, images, and code execution |
| `lifecycle/` | Human questions, goals, plan exit, reminders, and completion |
| `coordination/` | Thin adapters over Protocol/Runtime delivery |
| `observability/` | Capability, journal, and prior-session inspection |
| `deployment/` | Thin adapter over the provider-neutral deployment backend |
| `web/` | Retrieval, media, HTTP, and conversion |
| `execution/` | Programmatic multi-tool calling support |
| `evolution.py` | Version/list/rollback/unload operations for evolved components |

The directory says what a tool exposes, not where its backend must live. For example,
coordination Tool classes delegate to `agentevolver.protocol`/`agentevolver.runtime`, and
`deployment/deploy.py` delegates to `agentevolver.deploy`; moving those backends into Tool would
reverse dependency ownership.

Workflow definitions and domain-specific release state machines do not belong here.

Registration is not mounting: importing this package makes schemas discoverable, while each
Agent configuration still selects its smallest useful subset. There is intentionally no
universal "required tools" bundle. For example, the Website Builder mounts only shell, patch,
deploy, continuation, evolution, and completion; a browser participant mounts only completion.
