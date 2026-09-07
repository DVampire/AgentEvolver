# Plugin

The full lifecycle of one component type: what a plugin is, how to write one, how to
change one, and how to judge one. The contract below holds for all three.

## What it is

A plugin is a Python package, `{extension_root}/plugin/{name}/`, that groups related
operations against one external service behind a single roster entry. The framework's own
live under `agentevolver/plugins/default/{name}/` — 88 of them, which is where to look for
a worked example rather than guessing.

```
{name}/
├── __init__.py            REQUIRED — it is an importable package, not a loose directory
├── plugin.py              REQUIRED — the `Plugin` subclass; the loader reads this exact name
├── PLUGIN.md              REQUIRED — frontmatter + the body an agent reads to call the tools
├── tools/
│   ├── __init__.py        REQUIRED
│   └── {tool}.py          one `PluginTool` subclass per file
└── resources/             optional — `icon.svg` and anything else the manifest points at
```

**The contract**: the tool ids, which canvas nodes and workflow steps already carry; each
tool's declared `output` keys; and PLUGIN.md's `tools` / `implemented` counts matching
reality. A tool is addressed as `{plugin}.{tool}` — `PluginTool.id` builds it from the
owning plugin's `name` and the tool's own `name`, so renaming either breaks every reference.
Failures are returned via `_fail`, never raised, and every message is prefixed with the tool
id (`return self._fail(f"{self.id}: …")`), because a plugin roster is flat and an unprefixed
error does not say which tool produced it. Successes go through `_ok(message, **data)`.

## Layout

**PLUGIN.md frontmatter** carries these fields, and the loader reads them rather than the
class:

| field | meaning |
|---|---|
| `id` | the plugin's addressable name; matches the directory and `Plugin.name` |
| `name` | display name |
| `category` | roster grouping — `data`, `model`, `memory`, … |
| `type` | `tool` for a tool-bearing plugin |
| `icon` | path under the package, usually `resources/icon.svg` |
| `tools` | how many tools this plugin declares |
| `implemented` | how many are actually implemented; a mismatch with `tools` is a defect |
| `credentials` | env var names the service needs, `[]` when none |
| `requirements` | third-party imports the tools need |
| `version` | semantic version, quoted |

**`plugin.py` declares its tools by class, not by name**: `tools = (GitTool,
GitextractorTool,)`. The loader instantiates from that tuple, so a tool file that exists but
is missing from the tuple is invisible, and one listed but not importable breaks the whole
plugin.

**The `## Credentials` section of PLUGIN.md** is the part a roster listing omits and a full
read includes — put what an operator must set there, not in the body prose.

## Writing a new one

Copy the closest of the 88 built-ins rather than starting from an empty directory: pick one
with the same shape (a search plugin for a search plugin, a vector store for a vector store)
and replace its service calls. Keep `id`, the directory name and `Plugin.name` identical.

Write every tool's failure path through `_fail` with the id prefix, and give each tool an
`output` declaration that matches what it actually returns — canvas nodes bind to those keys,
so an undeclared key is unreachable and a declared-but-absent one is a runtime error in
someone else's graph.

Verify with `scripts/plugin/validate.py {path}`: it checks the package layout, the
frontmatter fields, and whether `tools` / `implemented` agree with the classes it can
actually import. Then exercise at least one tool for real — a plugin that has never been
called is a guess.

## Improving an existing one

The target is named in the task. Call `inspect_tool` first for its source path and
`enable_evolving`; a frozen plugin cannot be edited — report that and stop. Read
`plugin.py`, `PLUGIN.md` and the tool files before changing anything.

**Never renumber or rename a tool id** unless the task explicitly asks: canvas nodes and
workflow steps hold those strings, and they break silently. Adding a tool means adding its
file, adding the class to the `tools` tuple, and raising both `tools` and `implemented` in
the frontmatter — leaving the counts behind is the most common defect here. Re-run
`scripts/plugin/validate.py` after the edit.

## Evaluating one

Call `inspect_tool` (capability_type="plugin") on the target — it returns the instruction
plus registry facts. Score across:

1. **Interface Compliance** — package layout complete (`__init__.py`, `plugin.py`,
   `PLUGIN.md`, `tools/__init__.py`); `Plugin` subclass with `name` / `description` /
   `category`; every declared tool is a `PluginTool` subclass and appears in the `tools`
   tuple.
2. **Manifest Accuracy** — frontmatter `tools` / `implemented` match the importable classes;
   `id` matches the directory and `Plugin.name`; `credentials` lists exactly the env vars the
   code reads; `icon` resolves.
3. **Error Handling** — failures returned through `_fail`, never raised; every message
   prefixed with the tool id; a missing credential produces an actionable message rather than
   a traceback.
4. **Documentation Quality** — PLUGIN.md's body says what each tool does and what it returns;
   `## Credentials` names what an operator must set.
5. **Execution** — run at least one tool. Where credentials are unavailable, say so and
   report which checks that blocked rather than scoring them as passing.
