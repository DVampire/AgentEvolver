---
id: twelvelabs
name: TwelveLabs
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/twelvelabs
status: complete
version: "1.0.0"
tools: 7
requirements: [twelvelabs]
---

# TwelveLabs

Migrated from the Langflow **twelvelabs** bundle. This package is in the
**structure** phase: all 7 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `twelvelabs.convert_astra_results` | Convert Astra DB to Pegasus Input | Converts Astra DB search results to inputs compatible with T | structure |
| `twelvelabs.pegasus_index` | TwelveLabs Pegasus Index Video | Index videos using TwelveLabs and add the video_id to metada | structure |
| `twelvelabs.split_video` | Split Video | Split a video into multiple clips of specified duration. | structure |
| `twelvelabs.text_embeddings` | TwelveLabs Text Embeddings | Generate embeddings using TwelveLabs text embedding models. | structure |
| `twelvelabs.twelvelabs_pegasus` | TwelveLabs Pegasus | Chat with videos using TwelveLabs Pegasus API. | structure |
| `twelvelabs.video_embeddings` | TwelveLabs Video Embeddings | Generate embeddings from videos using TwelveLabs video embed | structure |
| `twelvelabs.video_file` | Video File | Load a video file in common video formats. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/twelvelabs/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
