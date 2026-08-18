# Writing a plugin

## What it is

A plugin is a directory, `{extension_root}/plugin/{name}/`, holding `plugin.py` (the
`Plugin` subclass — the loader reads that exact filename), `PLUGIN.md`, and `tools/` with one
`PluginTool` class per file. A tool is addressed as `{plugin}.{tool}`.

**The contract**: the tool ids, which canvas nodes and workflow steps already carry; each tool's
declared `output` keys; and PLUGIN.md's `tools` / `implemented` counts matching reality. Failures
are returned via `_fail`, never raised, and every message is prefixed with the tool id.

