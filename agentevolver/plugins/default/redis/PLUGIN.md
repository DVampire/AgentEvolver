---
id: redis
name: Redis
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/redis
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-community, langchain-openai]
---

# Redis

Migrated from the Langflow **redis** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `redis.redis` | Redis | Implementation of Vector Store using Redis | structure |
| `redis.redis_chat` | Redis Chat Memory | Retrieves and store chat messages from Redis. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/redis/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
