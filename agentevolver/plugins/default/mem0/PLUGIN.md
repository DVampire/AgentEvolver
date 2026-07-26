---
id: mem0
name: Mem0
kind: bundle
category: data
icon: lucide:Mem0
source: langflow/bundles/mem0
status: complete
version: "1.0.0"
tools: 1
requirements: []
---

# Mem0

Migrated from the Langflow **mem0** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `mem0.mem0_chat_memory` | Mem0 Chat Memory | Retrieves and stores chat messages using Mem0 memory storage | structure |

## Icon

Uses lucide glyph `Mem0` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/mem0/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
