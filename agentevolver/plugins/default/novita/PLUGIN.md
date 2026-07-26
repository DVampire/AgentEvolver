---
id: novita
name: Novita
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/novita
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# Novita

Migrated from the Langflow **novita** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `novita.novita` | Novita AI | Generates text using Novita AI LLMs (OpenAI compatible). | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/novita/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
