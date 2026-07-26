---
id: jigsawstack
name: JigsawStack
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/jigsawstack
status: complete
version: "1.0.0"
tools: 11
requirements: [jigsawstack]
---

# JigsawStack

Migrated from the Langflow **jigsawstack** bundle. This package is in the
**structure** phase: all 11 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `jigsawstack.ai_scrape` | AI Scraper | Scrape any website instantly and get consistent structured d | structure |
| `jigsawstack.ai_web_search` | AI Web Search | Effortlessly search the Web and get access to high-quality r | structure |
| `jigsawstack.file_read` | File Read | Read any previously uploaded file seamlessly from \
         | structure |
| `jigsawstack.file_upload` | File Upload | Store any file seamlessly on JigsawStack File Storage and us | structure |
| `jigsawstack.image_generation` | Image Generation | Generate an image based on the given text by employing AI mo | structure |
| `jigsawstack.nsfw` | NSFW Detection | Detect if image/video contains NSFW content | structure |
| `jigsawstack.object_detection` | Object Detection | Perform object detection on images using JigsawStack | structure |
| `jigsawstack.sentiment` | Sentiment Analysis | Analyze sentiment of text using JigsawStack AI | structure |
| `jigsawstack.text_to_sql` | Text to SQL | Convert natural language to SQL queries using JigsawStack AI | structure |
| `jigsawstack.text_translate` | Text Translate | Translate text from one language to another with support for | structure |
| `jigsawstack.vocr` | VOCR | Extract data from any document type in a consistent structur | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/jigsawstack/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
