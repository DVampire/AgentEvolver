---
name: research_resources_connector
description: Research resources — Grants.gov funding opportunity search and Antibody Registry (RRID) antibody lookups. Public APIs, no auth.
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
  - search_grants
  - search_antibodies
  - get_antibody
  - find_antibodies_by_catalog
  - get_antibody_registry_stats
action_schemas:
  find_antibodies_by_catalog:
    properties:
      catalog_number:
        title: Catalog Number
        type: string
      limit:
        default: 15
        title: Limit
        type: integer
    required:
    - catalog_number
    title: find_antibodies_by_catalogArguments
    type: object
  get_antibody:
    properties:
      antibody_id:
        title: Antibody Id
        type: string
    required:
    - antibody_id
    title: get_antibodyArguments
    type: object
  get_antibody_registry_stats:
    properties: {}
    title: get_antibody_registry_statsArguments
    type: object
  search_antibodies:
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
    title: search_antibodiesArguments
    type: object
  search_grants:
    properties:
      keyword:
        title: Keyword
        type: string
      limit:
        default: 15
        title: Limit
        type: integer
    required:
    - keyword
    title: search_grantsArguments
    type: object
action_descriptions:
  find_antibodies_by_catalog: "Find antibodies by vendor catalog number (via Antibody\
    \ Registry full-text search).\n\n    Args:\n        catalog_number: vendor catalog\
    \ number (e.g. \"ab290\").\n        limit: max matches (default 15)."
  get_antibody: "Get an antibody's details from the Antibody Registry by its id/RRID.\n\
    \n    Args:\n        antibody_id: numeric abId (e.g. \"3751761\") or RRID (e.g.\
    \ \"AB_2532057\")."
  get_antibody_registry_stats: Get Antibody Registry summary statistics (total antibodies,
    last update).
  search_antibodies: "Search the Antibody Registry (full-text) for research antibodies.\n\
    \n    Args:\n        query: search text (e.g. \"anti-GFP\", \"CD3 monoclonal\").\n\
    \        limit: max antibodies (default 15).\n    Returns 'RRID<TAB>name<TAB>vendor<TAB>catalog<TAB>target<TAB>species'\
    \ rows."
  search_grants: "Search Grants.gov federal funding opportunities by keyword.\n\n  \
    \  Args:\n        keyword: search text (e.g. \"cancer research\", \"microbiome\"\
    ).\n        limit: max opportunities (default 15).\n    Returns 'number<TAB>title<TAB>agency<TAB>status<TAB>closeDate'\
    \ rows."
---
# Research Resources

A self-contained MCP connector for research-support lookups over **public** APIs
(no authentication): **Grants.gov** (federal funding opportunities) and the
**Antibody Registry** (research antibodies / RRIDs).

## Tools

- `search_grants` — search Grants.gov funding opportunities by keyword. Args: `keyword`, `limit`.
- `search_antibodies` — full-text search the Antibody Registry. Args: `query`, `limit`.
- `get_antibody` — antibody details by id/RRID. Args: `antibody_id` (e.g. "3751761" or "AB_2532057").
- `find_antibodies_by_catalog` — find antibodies by vendor catalog number. Args: `catalog_number`, `limit`.
- `get_antibody_registry_stats` — registry totals and last-update date. No args.

## Typical workflow

1. `search_grants` to find funding opportunities relevant to a project.
2. `search_antibodies` / `find_antibodies_by_catalog` to identify a validated antibody and its
   RRID; `get_antibody` for its full record (vendor, clonality, target, citation).

## Notes

- Read-only; hits the public Grants.gov and Antibody Registry APIs, so responses depend on
  their uptime.
- The `connection` uses a relative `server.py` and `command: python`, which the connector manager resolves to absolute paths at load time, so no machine-specific paths are needed.
