---
id: datastax
name: DataStax
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/datastax
status: complete
version: "1.0.0"
tools: 10
requirements: [astrapy, cassio, langchain-astradb, langchain-openai, python-dotenv]
---

# DataStax

Migrated from the Langflow **datastax** bundle. This package is in the
**structure** phase: all 10 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `datastax.astradb_chatmemory` | Astra DB Chat Memory | Retrieves and stores chat messages from Astra DB. | structure |
| `datastax.astradb_cql` | Astra DB CQL | Create a tool to get transactional data from DataStax Astra  | structure |
| `datastax.astradb_data_api` | Astra DB Data API |  | structure |
| `datastax.astradb_graph` | Astra DB Graph | Implementation of Graph Vector Store using Astra DB | structure |
| `datastax.astradb_tool` | Astra DB Tool | Tool to run hybrid vector and metadata search on DataStax As | structure |
| `datastax.astradb_vectorize` | Astra Vectorize | Configuration options for Astra Vectorize server-side embedd | structure |
| `datastax.astradb_vectorstore` | Astra DB | Ingest and search documents in Astra DB | structure |
| `datastax.dotenv` | Dotenv | Load .env file into env vars | structure |
| `datastax.graph_rag` | Graph RAG | Graph RAG traversal for vector store. | structure |
| `datastax.hcd` | Hyper-Converged Database | Implementation of Vector Store using Hyper-Converged Databas | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/datastax/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
