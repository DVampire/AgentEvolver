---
name: wiki_search_skill
description: Search and read Wikipedia articles via mediawiki-mcp-server. Use when the user needs factual knowledge, definitions, historical context, or encyclopedic information on any topic.
version: 1.0.0
require_grad: false
---

# Wiki Search Skill

Query Wikipedia (English) for encyclopedic knowledge. Powered by mediawiki-mcp-server via stdio MCP protocol.

## Quick Start

1. Determine what information the user needs
2. Choose the appropriate command (search, page, summary, sections, search-and-read)
3. Run the script via bash and return the result

## Available Commands

### Search — find relevant articles

```bash
python scripts/wiki_search.py search "quantum computing" --limit 5
```

Returns a list of matching articles with titles and snippets. Use this when the user's query is broad or you need to find the right article title first.

**Note: only one search query is supported per command call.** To look up multiple terms, run separate commands for each term.

### Summary — get a concise overview

```bash
python scripts/wiki_search.py summary "Quantum_computing"
```

Returns the introductory summary of a page. Use this for quick factual lookups or definitions.

### Page — get full content

```bash
python scripts/wiki_search.py page "Quantum_computing"
```

Returns the full page content. Use this when the user needs detailed, comprehensive information.

### Sections — get page structure

```bash
python scripts/wiki_search.py sections "Quantum_computing"
```

Returns the section headings of a page. Use this to understand page structure before fetching specific sections.

### Search and Read — one-step lookup

```bash
python scripts/wiki_search.py search-and-read "quantum computing"
```

Searches and returns the top result's content. Use this for direct, single-query lookups.

### Generic Call — any MCP tool

```bash
python scripts/wiki_search.py call mediawiki_get_related '{"title": "Quantum_computing"}'
python scripts/wiki_search.py call mediawiki_get_categories '{"title": "Quantum_computing"}'
```

Call any of the 40 available MCP tools directly. Run `python scripts/wiki_search.py list-tools` to see all available tools.

## Workflow

```
Task Progress:
- [ ] Step 1: Understand what the user is asking about
- [ ] Step 2: Search Wikipedia for relevant articles
- [ ] Step 3: Retrieve the most relevant page/summary
- [ ] Step 4: Extract and format the answer
- [ ] Step 5: Return the result to the user
```

## Conditional Workflow

1. Determine query type:

   **User asks for a quick fact/definition?** -> Use `summary`
   **User asks for in-depth information?** -> Use `search` then `page`
   **User gives an exact topic name?** -> Use `search-and-read`
   **User wants to explore related topics?** -> Use `call mediawiki_get_related`

2. Handle ambiguity:

   **Search returns multiple relevant results?** -> Present top 3 titles and ask user to choose
   **Search returns no results?** -> Try rephrasing the query or suggest related terms

## Page Title Convention

Wikipedia page titles use underscores for spaces: `Quantum_computing`, `Machine_learning`, `United_States`.

When converting a user query to a page title:
- Replace spaces with underscores
- Capitalize the first letter
- Keep the rest as-is (Wikipedia is case-sensitive after the first letter)

## Configuration

The script reads config from `resources/config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `server_path` | `bin/mediawiki-mcp-server` | Path to the MCP server binary |
| `mediawiki_url` | `https://en.wikipedia.org/w/api.php` | MediaWiki API endpoint |

Override via environment variables: `WIKI_MCP_SERVER_PATH`, `MEDIAWIKI_URL`.
