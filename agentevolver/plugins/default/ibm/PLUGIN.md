---
id: ibm
name: IBM watsonx
kind: bundle
category: data
icon: lucide:DB2
source: langflow/bundles/ibm
status: complete
version: "1.0.0"
tools: 3
requirements: [ibm-db, langchain-db2, langchain-ibm, langchain-openai]
---

# IBM watsonx

Migrated from the Langflow **ibm** bundle. This package is in the
**structure** phase: all 3 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `ibm.db2_vector` | IBM Db2 Vector Store |  | structure |
| `ibm.watsonx` | IBM watsonx.ai | Generate text using IBM watsonx.ai foundation models. | structure |
| `ibm.watsonx_embeddings` | IBM watsonx.ai Embeddings | Generate embeddings using IBM watsonx.ai models. | structure |

## Icon

Uses lucide glyph `DB2` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/ibm/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
