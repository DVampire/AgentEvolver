---
name: biorxiv_connector
description: bioRxiv & medRxiv preprint servers — search preprints, fetch details, list categories, and check journal publication status.
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: streamable_http
  url: https://hcls.mcp.claude.com/biorxiv/mcp
actions:
  - search_preprints
  - get_preprint
  - get_categories
  - search_published_preprints
  - search_by_funder
  - get_content_statistics
  - get_usage_statistics
action_schemas:
  get_categories:
    additionalProperties: false
    properties: {}
    type: object
  get_content_statistics:
    additionalProperties: false
    properties:
      interval:
        default: monthly
        description: 'Statistics interval: ''monthly'' or ''yearly'''
        enum:
        - monthly
        - yearly
        type: string
    type: object
  get_preprint:
    additionalProperties: false
    properties:
      doi:
        description: DOI of the preprint (e.g., '10.1101/2024.01.01.123456' or 'https://doi.org/10.1101/2024.01.01.123456')
        type: string
      server:
        default: biorxiv
        description: 'Which server to query: ''biorxiv'' or ''medrxiv'''
        enum:
        - biorxiv
        - medrxiv
        type: string
    required:
    - doi
    type: object
  get_usage_statistics:
    additionalProperties: false
    properties:
      interval:
        default: monthly
        description: 'Statistics interval: ''monthly'' or ''yearly'''
        enum:
        - monthly
        - yearly
        type: string
    type: object
  search_by_funder:
    additionalProperties: false
    properties:
      category:
        anyOf:
        - enum:
          - animal behavior and cognition
          - biochemistry
          - bioengineering
          - bioinformatics
          - biophysics
          - cancer biology
          - cell biology
          - clinical trials
          - developmental biology
          - ecology
          - epidemiology
          - evolutionary biology
          - genetics
          - genomics
          - immunology
          - microbiology
          - molecular biology
          - neuroscience
          - paleontology
          - pathology
          - pharmacology and toxicology
          - physiology
          - plant biology
          - scientific communication and education
          - synthetic biology
          - systems biology
          - zoology
          type: string
        - type: 'null'
        default: null
        description: Subject category to filter by
      cursor:
        default: 0
        description: Pagination cursor (starts at 0)
        minimum: 0
        type: integer
      date_from:
        description: Start date in YYYY-MM-DD format (must be >= 2025-04-10)
        type: string
      date_to:
        description: End date in YYYY-MM-DD format
        type: string
      funder_ror_id:
        description: Funder ROR ID (9-character string, e.g., '02mhbdp94' for European Commission)
        type: string
      limit:
        default: 10
        description: Maximum results to return (1-100)
        maximum: 100
        minimum: 1
        type: integer
      server:
        default: biorxiv
        description: 'Which server to query: ''biorxiv'' or ''medrxiv'''
        enum:
        - biorxiv
        - medrxiv
        type: string
    required:
    - funder_ror_id
    - date_from
    - date_to
    type: object
  search_preprints:
    additionalProperties: false
    properties:
      category:
        anyOf:
        - enum:
          - animal behavior and cognition
          - biochemistry
          - bioengineering
          - bioinformatics
          - biophysics
          - cancer biology
          - cell biology
          - clinical trials
          - developmental biology
          - ecology
          - epidemiology
          - evolutionary biology
          - genetics
          - genomics
          - immunology
          - microbiology
          - molecular biology
          - neuroscience
          - paleontology
          - pathology
          - pharmacology and toxicology
          - physiology
          - plant biology
          - scientific communication and education
          - synthetic biology
          - systems biology
          - zoology
          type: string
        - type: 'null'
        default: null
        description: Subject category to filter by
      cursor:
        default: 0
        description: Pagination cursor for retrieving additional results (starts at 0)
        minimum: 0
        type: integer
      date_from:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Start date for search in YYYY-MM-DD format (e.g., '2024-01-01'). Use with date_to.
      date_to:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: End date for search in YYYY-MM-DD format (e.g., '2024-12-31'). Use with date_from.
      limit:
        default: 10
        description: Maximum number of results to return (1-100)
        maximum: 100
        minimum: 1
        type: integer
      recent_count:
        anyOf:
        - exclusiveMinimum: 0
          type: integer
        - type: 'null'
        default: null
        description: Search last ~90 days, limit results to N (e.g., 50). Use 'limit' for actual result count.
      recent_days:
        anyOf:
        - exclusiveMinimum: 0
          type: integer
        - type: 'null'
        default: null
        description: Get preprints from last N days (e.g., 30). Alternative to date range.
      server:
        default: biorxiv
        description: 'Which server to query: ''biorxiv'' (biological sciences) or ''medrxiv'' (medical sciences)'
        enum:
        - biorxiv
        - medrxiv
        type: string
    type: object
  search_published_preprints:
    additionalProperties: false
    properties:
      cursor:
        default: 0
        description: Pagination cursor (starts at 0)
        minimum: 0
        type: integer
      date_from:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Start date in YYYY-MM-DD format. Use with date_to.
      date_to:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: End date in YYYY-MM-DD format. Use with date_from.
      include_details:
        default: true
        description: If True, returns full metadata (authors, abstract). If False, returns summary only.
        type: boolean
      limit:
        default: 10
        description: Maximum results to return (1-100)
        maximum: 100
        minimum: 1
        type: integer
      publisher:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Filter by publisher DOI prefix (e.g., '10.1038'). If provided, searches a specific publisher endpoint.
      recent_count:
        anyOf:
        - exclusiveMinimum: 0
          type: integer
        - type: 'null'
        default: null
        description: Get N most recent published articles (e.g., 50).
      recent_days:
        anyOf:
        - exclusiveMinimum: 0
          type: integer
        - type: 'null'
        default: null
        description: Get published articles from last N days (e.g., 30).
      server:
        default: biorxiv
        description: 'Which preprint server(s) to search: ''biorxiv'' or ''medrxiv''. Default: ''biorxiv'''
        enum:
        - biorxiv
        - medrxiv
        type: string
    type: object
action_descriptions:
  get_categories: |-
    List all 27 bioRxiv subject categories for filtering searches.

    WHEN TO USE:
    - Before search_preprints to see valid category values
    - To understand research area classifications

    RETURNS: Category names and API-compatible format (e.g., 'cancer biology' -> 'cancer_biology')

    FULL LIST: animal behavior and cognition, biochemistry, bioengineering, bioinformatics, biophysics, cancer biology, cell biology, clinical trials, developmental biology, ecology, epidemiology, evolutionary biology, genetics, genomics, immunology, microbiology, molecular biology, neuroscience, paleontology, pathology, pharmacology and toxicology, physiology, plant biology, scientific communication and education, synthetic biology, systems biology, zoology
  get_content_statistics: |-
    Get bioRxiv submission statistics over time.

    WHEN TO USE:
    - Analyze bioRxiv growth trends
    - Track submission patterns (new vs revised papers)
    - Generate platform statistics reports

    RETURNS (per month/year): new_papers, revised_papers, new_authors, cumulative_papers, cumulative_authors, period (YYYY-MM or YYYY)

    INTERVALS: 'monthly' (default) or 'yearly'

    NOTE: Returns all historical data from bioRxiv inception to present
  get_preprint: |-
    Get complete metadata for a specific preprint by DOI.

    WHEN TO USE:
    - You have a DOI and need full details
    - Need abstract, authors, PDF URL, funding info
    - Checking if preprint was published in a journal

    RETURNS: title, all authors, corresponding author + institution, full abstract, category, license, version, PDF URL, web URL, funding details, published DOI (if available)

    DOI FORMATS (all accepted):
    - '10.1101/2024.01.15.123456'
    - 'https://doi.org/10.1101/2024.01.15.123456'

    SERVERS:
    - 'biorxiv': For bioRxiv preprints (DOI contains biorxiv dates like 2024.01.15)
    - 'medrxiv': For medRxiv preprints

    IMPORTANT: Preprints are NOT peer-reviewed

    RELATED: search_preprints (find DOIs), search_published_articles (find journal version)
  get_usage_statistics: |-
    Get bioRxiv usage/engagement statistics over time.

    WHEN TO USE:
    - Analyze readership trends
    - Track engagement metrics (views, downloads)
    - Compare abstract vs full-text vs PDF engagement

    RETURNS (per month/year): abstract_views, full_text_views, pdf_downloads, cumulative_abstract, cumulative_full_text, cumulative_pdf, period (YYYY-MM or YYYY)

    INTERVALS: 'monthly' (default) or 'yearly'

    NOTE: Returns all historical data from bioRxiv inception to present
  search_by_funder: |-
    Search for preprints funded by a specific organization using ROR ID.

    WHEN TO USE:
    - Track research output from specific funding bodies
    - Analyze funder-specific publication patterns
    - Monitor grant-funded research areas

    REQUIRES: funder_ror_id (9-character ROR ID)

    COMMON FUNDER ROR IDS:
    - '021nxhr62' - NIH (National Institutes of Health)
    - '01cwqze88' - NSF (National Science Foundation)
    - '02mhbdp94' - European Commission
    - '029chgv08' - Wellcome Trust
    - '05a28rw58' - HHMI (Howard Hughes Medical Institute)
    - '006wxqw41' - MRC (Medical Research Council UK)
    - '00f54p054' - BBSRC (UK)
    - '01s5ya894' - Chan Zuckerberg Initiative

    Find more ROR IDs at: https://ror.org/search

    RETURNS: DOI, title, authors, abstract preview, category (same as search_preprints)

    IMPORTANT: Date range required. Earliest date is 2025-04-10 (funder metadata inception)

    SERVERS: 'biorxiv' or 'medrxiv'
  search_preprints: |-
    Search bioRxiv/medRxiv preprints. Returns DOI, title, authors, abstract preview, category.

    WHEN TO USE:
    - Literature review and research discovery
    - Finding recent research in specific fields
    - Tracking new submissions by date or category

    SEARCH METHODS (use only ONE):
    - Date range: date_from + date_to (e.g., '2024-01-01' to '2024-06-30') - RECOMMENDED
    - Recent days: recent_days=30 (last 30 days)
    - Recent count: recent_count=50 (searches last ~90 days, returns up to 'limit' results)

    IMPORTANT: All searches use date ranges internally. recent_count searches a 90-day window.
    If no search method specified, defaults to last 60 days.

    SERVERS:
    - 'biorxiv': Biological sciences (default)
    - 'medrxiv': Medical/health sciences

    CATEGORIES (27 available):
    biochemistry, bioinformatics, cancer biology, cell biology, genetics, genomics, immunology, microbiology, molecular biology, neuroscience, and 17 more. Use get_categories tool for full list.

    LIMITATIONS:
    - NO keyword/text search - filter by category and date only
    - Results are NOT peer-reviewed preprints

    EXAMPLES:
    - Last 30 days of neuroscience: recent_days=30, category='neuroscience'
    - Cancer biology Q1 2024: date_from='2024-01-01', date_to='2024-03-31', category='cancer biology'

    PAGINATION: Use cursor for next page (cursor=100 for results 100-199)

    RELATED: get_preprint (full details by DOI), get_categories (list all categories)
  search_published_preprints: |-
    Find preprints that have been published in peer-reviewed journals.

    WHEN TO USE:
    - Track which preprints became journal articles
    - Find the peer-reviewed version of a preprint
    - Analyze preprint-to-publication patterns
    - Filter articles by whether they were published by a specific journal

    SEARCH METHODS (use only ONE):
    - Date range: date_from + date_to
    - Recent count: recent_count=50
    - Recent days: recent_days=30

    FILTERS:
    - include_details: True for full metadata (slower), False for summary (faster)
    - publisher: Filter by DOI prefix (e.g., '10.1038' for Nature) - see below

    COMMON PUBLISHER PREFIXES:
    - '10.1038' - Nature Publishing Group
    - '10.1126' - Science/AAAS
    - '10.1016' - Elsevier
    - '10.1371' - PLOS
    - '10.7554' - eLife
    - '10.1073' - PNAS

    EXAMPLE: Find recently published medRxiv papers: server='medrxiv', recent_days=30
---
# bioRxiv

An MCP connector for accessing the bioRxiv and medRxiv preprint servers (operated by
Cold Spring Harbor Laboratory). Wraps the official bioRxiv API to provide structured
access to preprint metadata, abstracts, and full-text PDFs. Note: preprints have **not**
undergone peer review.

## Tools

### search_preprints
Find preprints by date range and/or subject category. This is the primary discovery
tool — it does not support keyword, author, or full-text search.

### get_preprint
Get full details (metadata, abstract) for a specific preprint by DOI.

### get_categories
List the available subject categories that can be used to filter searches.

### search_published_preprints
Find preprints that have subsequently been formally published in peer-reviewed journals.

### search_by_funder
Find preprints by funding source (e.g. NIH), for research-funding pattern analysis.

### get_content_statistics
Submission-volume statistics for the preprint corpus.

### get_usage_statistics
Usage statistics (views/downloads) for the preprint servers.

## Typical workflow

1. `get_categories` to identify relevant subject areas.
2. `search_preprints` with a date range + category to find recent activity.
3. `get_preprint` for full details on promising hits.
4. `search_published_preprints` to check whether a preprint was later peer-reviewed.
