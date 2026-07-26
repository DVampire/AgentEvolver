---
id: docling
name: Docling
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/docling
status: complete
version: "1.0.0"
tools: 4
requirements: [docling]
---

# Docling

Migrated from the Langflow **docling** bundle. This package is in the
**structure** phase: all 4 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `docling.chunk_docling_document` | Chunk DoclingDocument | Use DoclingDocument chunkers to split the document into chun | structure |
| `docling.docling_inline` | Docling | Uses Docling to process input documents running the Docling  | structure |
| `docling.docling_remote` | Docling Serve | Uses Docling to process input documents connecting to your i | structure |
| `docling.export_docling_document` | Export DoclingDocument | Export DoclingDocument to markdown, html or other formats. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/docling/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
