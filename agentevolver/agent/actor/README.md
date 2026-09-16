---
name: agent_actor
description: "Contains built-in task-performing and orchestrating Agents: general, code, browser, reviewer, monitor, and MetaAgent. Actors solve user work; lifecycle-specific generation, evaluation, and optimization roles live in their sibling modules."
version: 1.0.0
type: module
category: agent
requirements: []
metadata: {}
---
# Agent actors

Contains built-in task-performing and orchestrating Agents: general, code, browser, reviewer,
monitor, and MetaAgent. Actors solve user work; lifecycle-specific generation, evaluation,
and optimization roles live in their sibling modules.

## Actor declarations and task lifecycle

`WebsiteBuilderAgent` declares identity, prompt, limits and capability scope. It inherits
the same preparation, action loop, planning and guards as `MetaAgent`. It has no website-specific startup, feedback or evolution bookkeeping methods.

| Responsibility | Owner |
|---|---|
| Product requirements, participant briefs and release count | Task/launcher configuration (`examples/run_website_evolution_demo.py`) |
| Attachment binding and public input projection | Base `Agent.prepare_task`, using `task.context` |
| Subscriber creation, contexts, permissions and cleanup | `Kernel.bootstrap_subscribers` / `dispatch` |
| Releases, previews, feedback collection and acceptance status | `deploy_tool` → `deployment_manager` |
| Exposing current feedback alongside background jobs | `JobEnvironment.get_state` |
| Working plan and feedback-driven replanning instructions | `plan_manager` and shared prompt rules |
| Active component versions and persisted adoption decisions | `adoption_tool` → `extension_manager` |

Business policy is checked when its capability is called, not when the Agent exits.
`deploy_tool(action="status")` reports unmet release requirements; deployment operations
validate their own preconditions. `adoption_tool` validates evidence and the exact active
version before recording a keep decision. The base loop and `done_tool` do not consult
these managers or veto a partial handoff.
