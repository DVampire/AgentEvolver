---
id: cometapi
name: CometAPI
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/cometapi
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# CometAPI

Migrated from the Langflow **cometapi** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `cometapi.cometapi` | CometAPI | All AI Models in One API 500+ AI Models | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/cometapi/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
