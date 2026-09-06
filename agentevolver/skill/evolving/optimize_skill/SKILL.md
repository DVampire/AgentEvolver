---
name: optimize_skill
description: How to improve an existing component of any of the eight types this framework can build — tool, skill, agent, connector, environment, memory, workflow, plugin. Use when editing a component that already exists, from execution evidence or an evaluation report. Carries one reference file per type saying what that type is, which parts of its contract must survive the edit, and how to verify the change. Read by optimize_agent.
version: 1.1.0
license: N/A
type: [worker]
category: meta
requirements: [cpu]
metadata: {}
---

# Optimize

How to change one existing component without breaking it. `target_type` says which kind,
`target_name` which one; read that type's file first.

| `target_type` | read |
|---|---|
| `tool` | `references/tool.md` |
| `skill` | `references/skill.md` |
| `agent` | `references/agent.md` |
| `connector` | `references/connector.md` |
| `environment` | `references/environment.md` |
| `memory` | `references/memory.md` |
| `workflow` | `references/workflow.md` |
| `plugin` | `references/plugin.md` |

## What every type shares

**Check the gate first.** `inspect_tool` with the target's type gives its source path
and `enable_evolving`. **Frozen means stop**: a frozen component cannot be optimized — the write is
refused at registration. Report that it is frozen and say a new component in `extension/` is the
way, rather than trying and failing.

**Read before you write.** Read the current source; never assume its contents. If related files
are listed, read those too — they may hold dependencies or tests your change affects.

**The smallest correct change.** Do the thing the task asks and nothing else; do not refactor
around it. Use the tools actually mounted for this run: `apply_patch_tool` for targeted
patches or `bash_tool` for scoped edits when available and authorized. Inspect exact source
context, keep unrelated changes intact, and overwrite only when a rewrite is genuinely
necessary. Do not request an unmounted file tool just to follow an editing example.

**Preserve the contract.** What the contract is, the type's file says — a tool's `__call__`
signature and `Response`, a skill's frontmatter, a plugin's tool ids, a workflow's declared
inputs. Change it only when the task explicitly asks, because everything already pointing at it
breaks silently.

**Write to the staging tree.** The improved version goes under `{extension_root}/{target_type}/`,
never over a file in `{package_root}`.

**Verify, then hand over the path.** Run the type's check after every edit, then any available
test or a quick functional call. Put the changed component's **absolute path** in
`done_tool.reasoning` — that is how the new version gets registered.
