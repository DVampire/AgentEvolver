---
id: couchbase
name: Couchbase
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/couchbase
status: complete
version: "1.0.0"
tools: 1
requirements: [couchbase, langchain-couchbase, langchain-openai]
---

# Couchbase

Migrated from the Langflow **couchbase** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `couchbase.couchbase` | Couchbase | Couchbase Vector Store with search capabilities | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/couchbase/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
