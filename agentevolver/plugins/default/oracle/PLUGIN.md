---
id: oracle
name: Oracle
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/oracle
status: complete
version: "1.0.0"
tools: 3
requirements: [langchain-community, langchain-openai, oracledb]
---

# Oracle

Migrated from the Langflow **oracle** bundle. This package is in the
**structure** phase: all 3 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `oracle.oracledb_embeddings` | Oracle Embeddings | Generate embeddings using Oracle AI Vector Search. | structure |
| `oracle.oracledb_loaders` | Oracle Doc Loader | Read documents from Oracle Database using OracleDocLoader. | structure |
| `oracle.oraclevs` | Oracle Vector Store | Oracle vector store with search capabilities | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/oracle/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
