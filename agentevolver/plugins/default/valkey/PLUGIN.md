---
id: valkey
name: Valkey
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/valkey
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-community, langchain-openai]
---

# Valkey

Migrated from the Langflow **valkey** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `valkey.valkey` | Valkey | Implementation of Vector Store using Valkey | structure |
| `valkey.valkey_chat` | Valkey Chat Memory | Retrieves and stores chat messages from Valkey. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/valkey/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
