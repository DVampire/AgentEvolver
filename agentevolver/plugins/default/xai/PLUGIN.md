---
id: xai
name: xAI
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/xai
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# xAI

Migrated from the Langflow **xai** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `xai.xai` | xAI | Generates text using xAI models like Grok. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/xai/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
