# Default connectors

Each sub-directory here defines one **connector** — a wrapper around a single MCP
server — via a `CONNECTOR.md` file. Mirrors `src/skill/default` (where each
sub-directory holds a `SKILL.md`): a YAML frontmatter block for metadata + the
connection config, followed by a markdown body that documents the module and each
of its tools (actions).

`connector_manager.initialize()` scans this directory (and the extension
directory), parses every `CONNECTOR.md`, and loads it. Parsing is offline: it
never opens a network connection, so a bad/unreachable server config will not
break startup. Actions listed under `actions:` in the frontmatter are shown in the
prompt context; call `connector_manager.discover("<name>")` to open a live session
and refresh the real action list.

## CONNECTOR.md format

**Frontmatter (YAML)** — parsed with a full YAML parser, so nested values like
`connection` are supported:

| field            | required | meaning                                                             |
|------------------|----------|---------------------------------------------------------------------|
| `name`           | yes      | Connector name (registry key); should match the directory name.     |
| `description`    | yes      | One-line description shown in the prompt context.                   |
| `version`        | no       | Defaults to `1.0.0`.                                                 |
| `type`           | no       | Free-form label (e.g. `worker`); defaults to `worker`.              |
| `permission_mode`| no       | `read_only` / `workspace_write` / `danger_full_access`.             |
| `connection`     | yes      | MCP connection config in `MultiServerMCPClient` format (nested).    |
| `actions`        | no       | Statically declared MCP tool names for prompt display.              |
| `action_schemas` | no       | Optional per-action argument schemas.                               |

Any other frontmatter keys are collected into `metadata`.

**Body (markdown)** — module intro + per-tool detailed docs. Stored on
`ConnectorConfig.content` and NOT injected into the prompt context wholesale; the
context only carries name/description/actions plus the `CONNECTOR.md` path, so an
agent can read the full body on demand (progressive disclosure, like `SKILL.md`).

Example — see [`biomart/CONNECTOR.md`](./biomart/CONNECTOR.md):

```markdown
---
name: biomart
description: Ensembl BioMart — genomic annotations, identifier translation, and cross-reference queries.
version: 1.0.0
type: worker
connection:
  transport: stdio
  command: python
  args:
    - ./servers/biomart_server.py
actions:
  - list_marts
  - query
---

# BioMart

Ensembl BioMart — ...

## Tools

### list_marts
Lists all available Biomart marts (databases) from Ensembl.
...
```

## Execution

A connector is self-contained (like a skill) — it is **not** registered into the
tool manager. Call it directly:

```python
await connector_manager(
    name="biomart",
    input={"action": "list_marts", "args": {}},
)
```
