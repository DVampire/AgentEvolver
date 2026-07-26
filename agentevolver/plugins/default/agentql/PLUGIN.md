---
id: agentql
name: AgentQL
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/agentql
status: complete
version: "1.0.0"
tools: 1
requirements: []
---

# AgentQL

Migrated from the Langflow **agentql** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `agentql.agentql_api` | Extract Web Data | Extracts structured data from a web page using an AgentQL qu | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/agentql/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
