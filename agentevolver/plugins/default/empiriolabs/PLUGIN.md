---
id: empiriolabs
name: EmpirioLabs
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/empiriolabs
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-openai]
---

# EmpirioLabs

Migrated from the Langflow **empiriolabs** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `empiriolabs.empiriolabs` | EmpirioLabs AI | Generates text using EmpirioLabs AI LLMs (OpenAI compatible) | structure |
| `empiriolabs.empiriolabs_image_generation` | EmpirioLabs AI Image Generation | Generate an image from a text prompt using EmpirioLabs AI im | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/empiriolabs/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
