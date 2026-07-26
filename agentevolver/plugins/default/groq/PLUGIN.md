---
id: groq
name: Groq
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/groq
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-groq]
---

# Groq

Migrated from the Langflow **groq** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `groq.groq` | Groq | Generate text using Groq. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/groq/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
