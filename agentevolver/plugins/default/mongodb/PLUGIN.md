---
id: mongodb
name: MongoDB
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/mongodb
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-mongodb, langchain-openai, pymongo]
---

# MongoDB

Migrated from the Langflow **mongodb** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `mongodb.mongodb_atlas` | MongoDB Atlas | MongoDB Atlas Vector Store with search capabilities | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/mongodb/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
