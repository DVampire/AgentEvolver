---
name: generate_skill
description: How to write a new component of any of the eight types this framework can build — tool, skill, agent, connector, environment, memory, workflow, plugin. Use when creating a component that does not exist yet. Carries one reference file per type with that type's layout, template, contract and verification command, plus the conventions every type shares — where to write, how registration happens, and what to verify before finishing. Read by generate_agent.
version: 1.0.0
license: N/A
type: [worker]
category: meta
requirements: [cpu]
metadata: {}
---

# Generate

How to write one new component. `target_type` says which of the eight kinds; read that file
before writing anything.

| `target_type` | read | the artifact is |
|---|---|---|
| `tool` | `references/tool.md` | one Python file |
| `skill` | `references/skill.md` | a directory with `SKILL.md` |
| `agent` | `references/agent.md` | a Python file, usually with an HTML prompt |
| `connector` | `references/connector.md` | a directory with `CONNECTOR.md` |
| `environment` | `references/environment.md` | a directory with `environment.py` + `ENVIRONMENT.md` |
| `memory` | `references/memory.md` | one Python file |
| `workflow` | `references/workflow.md` | one HTML file |
| `plugin` | `references/plugin.md` | a directory with `plugin.py` + `PLUGIN.md` |

Do not work from memory. The layouts differ, and a component in the wrong shape registers and
then fails at the moment something tries to use it. Where a type has a template under
`references/<type>/`, start from it rather than from a blank file.

## What every type shares

**Where things go.** Write to `{extension_root}/{target_type}/`, and nowhere else. That is this
session's staging tree; a component is promoted to the shared extension root only after
validation and explicit approval. `{package_root}` is read-only. Temporary verification scripts
go in `{workspace_root}` — never the project root.

**How registration happens.** You never edit an `__init__.py`, never touch a registry, and never
restart anything. Put the new component's **absolute path** in your `done_tool.reasoning`; the
`registration_hook` finds it there, promotes it and registers it. That path is
the whole handoff — a run that omits it fails at the last step with nothing installed, having
done all the work.

**Verify before you finish.** Every type has a check, named in its file: Python compiles
(`python -m py_compile /abs/path.py && echo "syntax OK"`), a manifest directory has its manifest
where the loader expects it, a workflow compiles. Then exercise what you built at least once — a
component that has never been run is a guess.

**Name it once.** Check the name is not already taken (`inspect_tool`) before writing.
A second component under an existing name is refused at registration if the first is frozen, and
silently replaces it if not.
