---
id: google
name: Google
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/google
status: complete
version: "1.0.0"
tools: 9
requirements: [google-cloud-bigquery, langchain-community, langchain-google-community, langchain-google-genai, langchain-openai]
---

# Google

Migrated from the Langflow **google** bundle. This package is in the
**structure** phase: all 9 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `google.gmail` | Gmail Loader | Loads emails from Gmail using provided credentials. | structure |
| `google.google_bq_sql_executor` | BigQuery | Execute SQL queries on Google BigQuery. | structure |
| `google.google_drive` | Google Drive Loader | Loads documents from Google Drive using provided credentials | structure |
| `google.google_drive_search` | Google Drive Search | Searches Google Drive files using provided credentials and q | structure |
| `google.google_generative_ai` | Google Generative AI | Generate text using Google Generative AI. | structure |
| `google.google_generative_ai_embeddings` | Google Generative AI Embeddings |  | structure |
| `google.google_oauth_token` | Google OAuth Token | Generates a JSON string with your Google OAuth token. | structure |
| `google.google_search_api_core` | Google Search API | Call Google Search API and return results as a DataFrame. | structure |
| `google.google_serper_api_core` | Google Serper API | Call the Serper.dev Google Search API. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/google/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
