---
name: tool_default_deployment
description: "Contains the model-facing deployment adapter while the deployment subsystem owns infrastructure lifecycle and profiles."
version: 1.0.0
type: module
category: tool
requirements: []
metadata: {}
---
# Deployment tools

Model-facing deployment verbs only. Provider discovery, container lifecycle, URL routing,
and framework profiles remain owned by `agentevolver.deploy`.

Product-specific release approval and evaluation state machines do not belong here.
