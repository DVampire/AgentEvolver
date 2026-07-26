---
id: anthropic
name: Anthropic
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/anthropic
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-anthropic]
---

# Anthropic

Migrated from the Langflow **anthropic** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `anthropic.anthropic` | Anthropic | Generate text using Anthropic | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/anthropic/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
