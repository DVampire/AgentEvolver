---
name: claude_api_skill
description: Reference for the Claude API / Anthropic SDK — model ids, pricing, params, streaming, tool use, MCP, agents, caching, token counting, model migration. Use when building or debugging apps that call the Claude API, choosing a model, or answering questions about Anthropic models — do not answer from memory.
version: 1.0.0
type: worker
license: N/A
category: reference
requirements: [cpu]
metadata: {}
---

# Claude API Skill

Reference for the Claude API / Anthropic SDK. Load this **before** answering anything LLM-shaped about Anthropic models — knowledge from memory is stale by default.

## When to use (TRIGGER)

Read this before opening the target file — don't skip because it "looks like a one-liner" — whenever:

- The prompt names Claude/Anthropic in any form (Claude, Anthropic, Fable, Opus, Sonnet, Haiku, `anthropic`, `@anthropic-ai`, `claude-*`, `us.anthropic.*`, `[1m]`).
- The user asks about an LLM (pricing / model choice / limits / caching) — never answer from memory.
- The task is LLM-shaped with provider unstated: agent / MCP / tool-definition / multi-agent / RAG / LLM-judge / computer-use; generate / summarize / extract / classify / rewrite / converse over natural language; debugging refusals / cutoffs / streaming / tool-calls / tokens.

## When to skip (overrides all triggers)

- Another provider is named in the query: OpenAI / GPT / Gemini / Llama / Mistral / Cohere / Ollama.
- Or `grep -rE 'openai|langchain_openai|google.generativeai|genai|mistralai|cohere|ollama'` over the project hits (run this grep FIRST if no provider is named — don't Read the file).

## Current model IDs

| Role | Model ID | Name |
|---|---|---|
| Most capable | `claude-opus-4-8` | Claude Opus 4.8 |
| Balanced | `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| Fast / cheap | `claude-haiku-4-5` | Claude Haiku 4.5 |
| Creative | `claude-fable-5` | Claude Fable 5 |
| (previous Sonnet) | `claude-sonnet-4-5` | — |

When building AI applications, default to the latest and most capable models. For scheduled/background agents, a balanced default like `claude-sonnet-4-6` is reasonable.

## How to answer

1. Detect the project language from its files (`.py`/`requirements.txt`/`pyproject.toml` → Python; `.ts`/`package.json` → TypeScript; `.go`/`go.mod` → Go; also Java, Ruby, C#, PHP; else raw cURL).
2. Pull the matching SDK reference for the topic the user needs: client setup, streaming, tool use, batches, files API, prompt caching, token counting, MCP integration, managed/scheduled agents, model migration, error codes, platform availability (incl. AWS Bedrock / Vertex).
3. Answer from that reference, not from memory. Cite the exact param/field names.

> The upstream skill ships full per-language SDK docs (Python, TypeScript, Go, Java, Ruby, C#, PHP, cURL) plus shared docs (agent-design, prompt-caching, token-counting, tool-use-concepts, model-migration, models, error-codes, managed-agents-*). Fetch the authoritative current docs from the Anthropic docs site when you need depth beyond this summary.
