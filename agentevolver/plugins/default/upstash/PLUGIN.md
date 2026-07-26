---
id: upstash
name: Upstash
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/upstash
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community, langchain-openai]
---

# Upstash

Migrated from the Langflow **upstash** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `upstash.upstash` | Upstash | Upstash Vector Store with search capabilities | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/upstash/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
