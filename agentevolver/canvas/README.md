---
name: canvas
description: "Visual editor over the workflow module: flow graphs are JSON source that compiles into <workflow> HTML, registers with workflow_manager, and runs on the workflow runtime."
version: 1.0.0
type: module
category: orchestration
requirements: []
metadata:
  document_version: 2
---
# Canvas

The canvas powers the visual workflow editor in the web UI. It owns **no
executor**: the graph JSON is the editable source of truth, publishing
compiles it into the same `<workflow>` HTML used by hand-written workflows and
registers it with `workflow_manager`, and every run (draft or published)
executes on the workflow runtime.

## Modules

| Module | Responsibility |
|---|---|
| `types.py` | Flow graph documents (nodes/edges), palette specs |
| `compiler.py` | Graph → `<workflow>` HTML, validated by the real `WorkflowCompiler` |
| `catalog.py` | Palette: structural steps, io nodes, live tool/agent/workflow entries |
| `server.py` | `canvas_manager`: JSON drafts, publish/register, ephemeral draft runs |

## Behaviour

- Flows persist as JSON under `<home>/canvas/flows/`; published artifacts as
  HTML under `<home>/canvas/workflows/` and are re-registered at startup.
- Invocation params live in the HTML (`<arg>` + step attributes); capability
  configuration stays in the registries; visual state stays in the JSON.
- Publishing auto-bumps the patch version when content changes; drift against
  the registered workflow is reported, never silently merged.

See `docs/canvas.md` for the full design, wire contracts, and v1 limits.
