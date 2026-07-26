---
id: amazon
name: Amazon
kind: bundle
category: data
icon: lucide:Amazon
source: langflow/bundles/amazon
status: complete
version: "1.0.0"
tools: 4
requirements: [boto3, langchain-aws, langchain-openai]
---

# Amazon

Migrated from the Langflow **amazon** bundle. This package is in the
**structure** phase: all 4 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `amazon.amazon_bedrock_converse` | Amazon Bedrock Converse |  | structure |
| `amazon.amazon_bedrock_embedding` | Amazon Bedrock Embeddings | Generate embeddings using Amazon Bedrock models. | structure |
| `amazon.amazon_bedrock_model` | Amazon Bedrock |  | structure |
| `amazon.s3_bucket_uploader` | S3 Bucket Uploader | Uploads files to S3 bucket. | structure |

## Icon

Uses lucide glyph `Amazon` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/amazon/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
