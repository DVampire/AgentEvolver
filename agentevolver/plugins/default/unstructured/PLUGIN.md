---
id: unstructured
name: Unstructured
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/unstructured
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-unstructured]
---

# Unstructured

Migrated from the Langflow **unstructured** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `unstructured.unstructured` | Unstructured API |  | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/unstructured/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
