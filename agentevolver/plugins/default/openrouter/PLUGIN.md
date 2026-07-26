---
id: openrouter
name: OpenRouter
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/openrouter
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# OpenRouter

Migrated from the Langflow **openrouter** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `openrouter.openrouter` | OpenRouter |  | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/openrouter/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
