---
id: wolframalpha
name: WolframAlpha
kind: bundle
category: data
icon: lucide:WolframAlphaAPI
source: langflow/bundles/wolframalpha
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# WolframAlpha

Migrated from the Langflow **wolframalpha** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `wolframalpha.wolfram_alpha_api` | WolframAlpha API |  | structure |

## Icon

Uses lucide glyph `WolframAlphaAPI` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/wolframalpha/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
