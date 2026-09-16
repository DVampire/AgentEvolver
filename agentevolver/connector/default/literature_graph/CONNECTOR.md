---
name: literature_graph_connector
description: Scholarly literature graph — OpenAlex works/citations/references/authors/venues plus arXiv preprint search. Public APIs, no auth.
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: stdio
  command: python
  args:
    - server.py
actions:
  - openalex_search_works
  - openalex_get_work
  - openalex_citations
  - openalex_references
  - openalex_search_authors
  - openalex_get_author
  - openalex_venue_info
  - arxiv_search
  - arxiv_get_papers
action_schemas:
  arxiv_get_papers:
    properties:
      arxiv_ids:
        title: Arxiv Ids
        type: string
      limit:
        default: 10
        title: Limit
        type: integer
    required:
    - arxiv_ids
    title: arxiv_get_papersArguments
    type: object
  arxiv_search:
    properties:
      limit:
        default: 10
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: arxiv_searchArguments
    type: object
  openalex_citations:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      work_id:
        title: Work Id
        type: string
    required:
    - work_id
    title: openalex_citationsArguments
    type: object
  openalex_get_author:
    properties:
      author_id:
        title: Author Id
        type: string
    required:
    - author_id
    title: openalex_get_authorArguments
    type: object
  openalex_get_work:
    properties:
      work_id:
        title: Work Id
        type: string
    required:
    - work_id
    title: openalex_get_workArguments
    type: object
  openalex_references:
    properties:
      limit:
        default: 25
        title: Limit
        type: integer
      work_id:
        title: Work Id
        type: string
    required:
    - work_id
    title: openalex_referencesArguments
    type: object
  openalex_search_authors:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: openalex_search_authorsArguments
    type: object
  openalex_search_works:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: openalex_search_worksArguments
    type: object
  openalex_venue_info:
    properties:
      query:
        title: Query
        type: string
    required:
    - query
    title: openalex_venue_infoArguments
    type: object
action_descriptions:
  arxiv_get_papers: |-
    Fetch specific arXiv papers by their ids.

    Args:
        arxiv_ids: comma-separated arXiv ids (e.g. "2202.07171,1706.03762").
        limit: max papers (default 10).
  arxiv_search: |-
    Search arXiv preprints by query.

    Args:
        query: search text (e.g. "diffusion models"); supports arXiv field prefixes
            like "au:", "ti:", "cat:".
        limit: max papers (default 10).
  openalex_citations: |-
    List works that CITE a given work (incoming citations).

    Args:
        work_id: OpenAlex work id (e.g. "W2556159813").
        limit: max citing works (default 20).
  openalex_get_author: |-
    Get an author's profile (works, citations, institution, top topics).

    Args:
        author_id: OpenAlex author id (e.g. "A5085943412").
  openalex_get_work: |-
    Get a work's full metadata (authors, venue, year, citations, abstract).

    Args:
        work_id: OpenAlex id (e.g. "W2556159813") or a DOI.
  openalex_references: |-
    List the works REFERENCED BY a given work (its bibliography).

    Args:
        work_id: OpenAlex work id (e.g. "W2556159813").
        limit: max references (default 25).
  openalex_search_authors: |-
    Search authors in OpenAlex by name.

    Args:
        query: author name (e.g. "Jennifer Doudna").
        limit: max authors (default 15).
    Returns 'id<TAB>name<TAB>works<TAB>citations<TAB>institution' rows.
  openalex_search_works: |-
    Search scholarly works (papers) in OpenAlex by keyword.

    Args:
        query: search text (e.g. "CRISPR gene editing").
        limit: max works (default 15).
    Returns 'id<TAB>title<TAB>year<TAB>citations<TAB>doi' rows.
  openalex_venue_info: |-
    Get info on a publication venue/source (journal) by name or OpenAlex source id.

    Args:
        query: venue name (e.g. "Nature") or a source id (e.g. "S137773608").
---
# Literature Graph

A self-contained MCP connector for the scholarly literature graph, over two **public**
APIs (no authentication): **OpenAlex** (`api.openalex.org`) and **arXiv**
(`export.arxiv.org`). Endpoint mappings referenced from open-source OpenAlex/arXiv MCP
servers (e.g. [benedict2310/Scientific-Papers-MCP](https://github.com/benedict2310/Scientific-Papers-MCP)).

## Tools

### OpenAlex
- `openalex_search_works` — search papers by keyword. Args: `query`, `limit`.
- `openalex_get_work` — full metadata + abstract for a work. Args: `work_id` (OpenAlex id or DOI).
- `openalex_citations` — works that cite a work (incoming). Args: `work_id`, `limit`.
- `openalex_references` — works referenced by a work (its bibliography). Args: `work_id`, `limit`.
- `openalex_search_authors` — search authors by name. Args: `query`, `limit`.
- `openalex_get_author` — author profile (works, citations, h-index, topics). Args: `author_id`.
- `openalex_venue_info` — journal/venue info by name or source id. Args: `query`.

### arXiv
- `arxiv_search` — search preprints (supports `au:`/`ti:`/`cat:` prefixes). Args: `query`, `limit`.
- `arxiv_get_papers` — fetch specific papers by arXiv id. Args: `arxiv_ids` (comma-separated), `limit`.

## Typical workflow

1. `openalex_search_works` / `arxiv_search` to find papers on a topic.
2. `openalex_get_work` for metadata + abstract; `openalex_citations` / `openalex_references`
   to walk the citation graph.
3. `openalex_search_authors` → `openalex_get_author` for researcher profiles;
   `openalex_venue_info` for journal metrics.

## Notes

- Read-only; hits public OpenAlex + arXiv APIs (OpenAlex "polite pool" via a mailto UA),
  so responses depend on their uptime.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
