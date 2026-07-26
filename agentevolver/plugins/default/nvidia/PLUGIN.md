---
id: nvidia
name: NVIDIA
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/nvidia
status: complete
version: "1.0.0"
tools: 5
requirements: [langchain-nvidia-ai-endpoints, langchain-openai, nv-ingest-client]
---

# NVIDIA

Migrated from the Langflow **nvidia** bundle. This package is in the
**structure** phase: all 5 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `nvidia.nvidia` | NVIDIA | Generates text using NVIDIA LLMs. | structure |
| `nvidia.nvidia_embedding` | NVIDIA Embeddings | Generate embeddings using NVIDIA models. | structure |
| `nvidia.nvidia_ingest` | NVIDIA Retriever Extraction | Multi-modal data extraction from documents using NVIDIA | structure |
| `nvidia.nvidia_rerank` | NVIDIA Rerank | Rerank documents using the NVIDIA API. | structure |
| `nvidia.system_assist` | NVIDIA System-Assist |  | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/nvidia/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
