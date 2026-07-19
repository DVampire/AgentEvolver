---
name: tool_default
description: "Contains the framework's atomic tools for files, shell, git, search, inspection, evolution, deployment, task completion, and related operations. Each Tool exposes a narrow callable contract and is registered through the parent Tool Manager."
version: 0.1.0
type: collection
category: tool
requirements: []
metadata:
  tracks_package_version: true
---
# Built-in tools

Contains the framework's atomic tools for files, shell, git, search, inspection, evolution,
deployment, task completion, and related operations. Each Tool exposes a narrow callable
contract and is registered through the parent Tool Manager.

Workflow definitions belong in `agentevolver/workflow/default/`, not in this collection.
