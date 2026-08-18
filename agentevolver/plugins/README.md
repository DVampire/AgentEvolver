---
name: plugins
description: "Outside services — OpenAI, Chroma, Tavily, YouTube, Composio — wrapped as plugins. One plugin per service, providing several tools; each tool is a canvas datasource node returning the canonical {message, data, files} envelope."
version: 1.0.0
type: module
category: plugins
requirements: []
metadata: {}
---
# Plugins

A **plugin** wraps one outside service and provides the tools that talk to it.
The shape is the same one `environment` uses — one class exposing many
capabilities:

| Container | Capabilities | Registry entries |
|---|---|---|
| `BrowserEnvironment` | `@action` click / scroll / type / … | 1 |
| `TavilyPlugin` | `TavilySearchTool`, `TavilyExtractTool` | 1 |

The plugin owns whatever its tools share: the credential, an HTTP client, a base
URL. A key is resolved once per plugin instead of once per call, and a
connection pool outlives the request that opened it.

A plugin is never itself a workflow step. Its *tools* surface on the canvas as
`datasource` nodes and are dispatched through `plugin_manager`.

## Reaching a plugin

A plugin's tools are addressed `<plugin>.<tool>` and reach a model, a canvas node or a
workflow step by three routes:

| Caller | Address | Notes |
|---|---|---|
| Agent (native function calling) | `tavily__tavily_search` | Opt-in: a plugin is projected only when a run names it in `plugin_allowlist` |
| Canvas node / workflow `datasource` | `tavily.tavily_search` | Dispatched through `plugin_manager` |
| Direct | `await plugin_manager(name="tavily", action="tavily_search", input=…)` | `action` names the member, as it does for `environment_manager` |

The agent side is opt-in on purpose. Every other capability's resident set is chosen in
config and numbers in the tens; this registry holds hundreds of tools for services most
runs never touch, so `get_instruction` and `function_callings` read an absent allowlist as
**none** rather than as all. Only implemented tools are projected — a registered stub is
honest on the canvas but has nothing to offer a model.

## A plugin is a package

```
default/tavily/
├── PLUGIN.md          # generated manifest: tools, status, credentials, requirements
├── __init__.py        # from .plugin import TavilyPlugin
├── plugin.py          # the one registered class: identity + shared credential/client
├── resources/icon.svg
└── tools/
    ├── search.py      # class TavilySearchTool(PluginTool)
    └── extract.py     # class TavilyExtractTool(PluginTool)
```

One tool per file, one class per tool — the same layout `tool/default/` uses. The same
layout is what `extension/plugin/<name>/` holds, so a plugin someone installs is the same
shape as a built-in one; `ExtensionManager` loads `plugin.py` and registers the class it
finds through `plugin_manager.register`.

```python
# plugin.py
from .tools.extract import TavilyExtractTool
from .tools.search import TavilySearchTool

@PLUGIN.register_module(force=True)
class TavilyPlugin(Plugin):
    tools = (TavilySearchTool, TavilyExtractTool)

    name: str = "tavily"
    display_name: str = "Tavily"
    category: str = "data"
```

Adding a plugin means adding one line to `default/__init__.py`; that import is
what runs the `@PLUGIN.register_module` decorator, so the file doubles as the
registry's manifest. (Explicit, like `tool/default/__init__.py` — not a
`pkgutil` scan, so a broken package fails loudly at import instead of vanishing
from the palette.)

## Addressing and dispatch

A tool is addressed as `<plugin>.<tool>`:

```
canvas node / workflow datasource step  →  target "tavily.tavily_search"
  → plugin_manager splits on the dot
  → TavilyPlugin.invoke("tavily_search", query=…)
  → TavilySearchTool(query=…)  →  Response
```

A bare plugin name works too, and falls through to the plugin's only tool — so a
single-capability plugin such as `yahoo` keeps a natural target.

## Module layout

| File | Holds |
|---|---|
| `types.py` | `Plugin`, `PluginTool`, `PluginConfig`, `PluginContext`, and the family templates |
| `context.py` | `PluginContextManager` — registry → `PluginConfig` → instance, lifecycle, dispatch |
| `server.py` | `PluginManagerServer` — the thin façade the rest of the framework calls |

Same split as `tool` / `environment` / `connector`, and the same five bands in the same
order: lifecycle, registration, query, contract, execution. One addition is a container's:
`list_infos()` is the batch form of `list()` + `get_info()`, because building the palette
wants every plugin's every tool and a hundred round trips to assemble one list is the
reason it exists (`process` has it for the same reason). For a single lookup, prefer
`list()` + `get_info(name)` — that is the enumeration idiom everywhere else.

Plugins wrap third-party services, so the evolution half of those managers
(`update` / `copy` / `restore`) has no counterpart here: rewriting a vendor's API
adapter at runtime is not something the optimizer should do.

## Family templates

Many services differ only in which client object gets constructed. `types.py`
holds that loop once, so a concrete tool supplies only the provider-specific
part — usually a single `_model` or `_build` method:

`LLMPluginTool` · `EmbeddingPluginTool` · `RerankPluginTool` ·
`VectorStorePluginTool` · `MemoryPluginTool` · `ComposioPluginTool`

## A result has two halves

`Response.data` is the machine contract — what the next canvas node wires to — and
`Response.message` is the sentence the model reads. A tool declares the first and derives
the second:

```python
class TavilySearchTool(PluginTool):
    output = {"query": "text", "answer": "text", "records": "list", "count": "any"}

    def _render(self, data):
        return f"Tavily returned {data['count']} result(s) for '{data['query']}'."

    async def __call__(self, ...):
        return self._ok(query=query, answer=..., records=records, count=len(records))
```

`output` keys become typed sub-ports on the canvas node (`${node.data.records}`), so a
downstream step can take the field it wants instead of the whole opaque object, and they
are listed in `PLUGIN.md`. `_render` writes the message when `_ok` is given none, which
keeps the prose a function of the data rather than a second thing to maintain — and keeps
a huge payload in `data` while the sentence about it stays a sentence.

Both are optional. A tool that has not been migrated passes its own message to `_ok` and
keeps the single `data` port it always had.

## Status is computed, not declared

`PluginTool.status` is derived from whether the class (or a family template it
builds on) actually overrides `__call__`. It used to be a hand-written field,
which meant a manifest could claim a tool worked when it only inherited the
stub. A tool that is registered but unimplemented still appears on the canvas
and returns a clear "not implemented yet" when run.

## Dependencies

Provider SDKs are imported lazily inside `__call__`, so a plugin registers
without them and a call returns a clear failed result until they are installed.
Each `PLUGIN.md` lists what that package needs under `requirements:`, and the
credentials it reads under `credentials:`. Declare both on the class
(`Plugin.requirements` / `Plugin.credentials`) — they are the two facts a manifest
cannot derive — and run `scripts/gen_plugin_manifest.py` to rewrite the derivable
half of every manifest from the live registry. `--undeclared` lists the plugins
still carrying hand-written values, beside the environment variables their
`_secret` calls actually read; `--check` fails when a manifest has drifted.
