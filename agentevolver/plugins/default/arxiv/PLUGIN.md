---
id: arxiv
name: Arxiv
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/arxiv
status: complete
version: "1.0.0"
tools: 1
requirements: []
---

# Arxiv

Migrated from the Langflow **arxiv** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `arxiv.arxiv` | arXiv | Search and retrieve papers from arXiv.org. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/arxiv/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
