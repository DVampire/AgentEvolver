---
id: needle
name: Needle
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/needle
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community, langchain-openai]
---

# Needle

Migrated from the Langflow **needle** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `needle.needle` | Needle Retriever | A retriever that uses the Needle API to search collections. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/needle/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
