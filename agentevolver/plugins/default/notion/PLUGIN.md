---
id: notion
name: Notion
kind: bundle
category: data
icon: lucide:NotionDirectoryLoader
source: langflow/bundles/notion
status: complete
version: "1.0.0"
tools: 8
requirements: []
---

# Notion

Migrated from the Langflow **notion** bundle. This package is in the
**structure** phase: all 8 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `notion.add_content_to_page` | Add Content to Page  | Convert markdown text to Notion blocks and append them to a  | structure |
| `notion.create_page` | Create Page  | A component for creating Notion pages. | structure |
| `notion.list_database_properties` | List Database Properties  | Retrieve properties of a Notion database. | structure |
| `notion.list_pages` | List Pages  |  | structure |
| `notion.list_users` | List Users  | Retrieve users from Notion. | structure |
| `notion.page_content_viewer` | Page Content Viewer  | Retrieve the content of a Notion page as plain text. | structure |
| `notion.search` | Search  | Searches all pages and databases that have been shared with  | structure |
| `notion.update_page_property` | Update Page Property  | Update the properties of a Notion page. | structure |

## Icon

Uses lucide glyph `NotionDirectoryLoader` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/notion/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
