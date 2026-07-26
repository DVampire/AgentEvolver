---
id: langwatch
name: LangWatch
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/langwatch
status: complete
version: "1.0.0"
tools: 1
requirements: []
---

# LangWatch

Migrated from the Langflow **langwatch** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `langwatch.langwatch` | LangWatch Evaluator | Evaluates various aspects of language models using LangWatch | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/langwatch/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
