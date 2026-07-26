---
id: sambanova
name: SambaNova
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/sambanova
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# SambaNova

Migrated from the Langflow **sambanova** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `sambanova.sambanova` | SambaNova | Generate text using Sambanova LLMs. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/sambanova/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
