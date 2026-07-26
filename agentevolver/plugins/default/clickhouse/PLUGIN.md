---
id: clickhouse
name: Clickhouse
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/clickhouse
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community, langchain-openai]
---

# Clickhouse

Migrated from the Langflow **clickhouse** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `clickhouse.clickhouse` | ClickHouse | ClickHouse Vector Store with search capabilities | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/clickhouse/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
