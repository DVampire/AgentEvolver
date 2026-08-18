---
name: genes_ontologies_connector
description: Gene identity and ontologies — MyGene queries, OLS4 ontology terms, GO annotations (QuickGO), UniProt entries, Reactome pathways. Aggregates five public APIs (no auth).
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
  - query_genes
  - list_ontologies
  - search_ontology_terms
  - get_ontology_term
  - get_go_annotations
  - get_uniprot_entries
  - map_reactome_pathways
action_schemas:
  get_go_annotations:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - gene
    title: get_go_annotationsArguments
    type: object
  get_ontology_term:
    properties:
      ontology:
        default: ''
        title: Ontology
        type: string
      term_id:
        title: Term Id
        type: string
    required:
    - term_id
    title: get_ontology_termArguments
    type: object
  get_uniprot_entries:
    properties:
      limit:
        default: 5
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: get_uniprot_entriesArguments
    type: object
  list_ontologies:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
    title: list_ontologiesArguments
    type: object
  map_reactome_pathways:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - gene
    title: map_reactome_pathwaysArguments
    type: object
  query_genes:
    properties:
      limit:
        default: 10
        title: Limit
        type: integer
      query:
        title: Query
        type: string
      species:
        default: human
        title: Species
        type: string
    required:
    - query
    title: query_genesArguments
    type: object
  search_ontology_terms:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      ontology:
        default: ''
        title: Ontology
        type: string
      query:
        title: Query
        type: string
    required:
    - query
    title: search_ontology_termsArguments
    type: object
action_descriptions:
  get_go_annotations: "Get Gene Ontology annotations for a gene/protein via QuickGO.\n\
    \n    Args:\n        gene: gene symbol or UniProt accession (e.g. \"TP53\" or \"\
    P04637\").\n        limit: max annotations (default 25).\n    Returns 'GO_id<TAB>aspect<TAB>qualifier<TAB>evidence'\
    \ rows (deduplicated)."
  get_ontology_term: "Get an ontology term's label, definition, and synonyms from EBI\
    \ OLS.\n\n    Args:\n        term_id: an OBO id (e.g. \"GO:0006915\", \"HP:0001250\"\
    ). Ontology is inferred\n            from the prefix if not given.\n        ontology:\
    \ OLS ontology id (optional; inferred from term_id prefix otherwise)."
  get_uniprot_entries: "Get UniProt protein entries by accession or a search query (human,\
    \ reviewed).\n\n    Args:\n        query: UniProt accession (e.g. \"P04637\") or\
    \ gene/protein search text (e.g. \"TP53\").\n        limit: max entries (default\
    \ 5)."
  list_ontologies: "List ontologies available in EBI OLS (id + title).\n\n    Args:\n\
    \        limit: max ontologies (default 30)."
  map_reactome_pathways: "Map a gene/protein to Reactome pathways it participates in.\n\
    \n    Args:\n        gene: gene symbol or UniProt accession (e.g. \"TP53\" or \"\
    P04637\").\n        limit: max pathways (default 25).\n    Returns 'stId<TAB>pathway'\
    \ rows."
  query_genes: "Query genes by symbol/name/id via MyGene.info; returns cross-reference\
    \ identifiers.\n\n    Args:\n        query: gene symbol, name, or id (e.g. \"TP53\"\
    , \"CDK2\").\n        species: species (default \"human\").\n        limit: max\
    \ hits (default 10).\n    Returns 'symbol<TAB>name<TAB>entrez<TAB>ensembl<TAB>uniprot'\
    \ rows."
  search_ontology_terms: "Search ontology terms across EBI OLS, optionally restricted\
    \ to one ontology.\n\n    Args:\n        query: search text (e.g. \"apoptosis\"\
    ).\n        ontology: OLS ontology id to restrict to (e.g. \"go\", \"hp\"); empty\
    \ for all.\n        limit: max results (default 15).\n    Returns 'term_id<TAB>label<TAB>ontology'\
    \ rows."
---
# Genes & Ontologies

A self-contained MCP connector for gene identity and ontologies, aggregating five
**public** APIs (no authentication): **MyGene.info**, **EBI OLS4**, **QuickGO**,
**UniProt**, and **Reactome**.

## Tools

### query_genes
Query genes by symbol/name/id (MyGene.info); returns cross-reference ids
(Entrez, Ensembl, UniProt).
- `query` (str), `species` (str, default "human"), `limit` (int, optional).

### list_ontologies
List ontologies available in EBI OLS (id + title).
- `limit` (int, optional).

### search_ontology_terms
Search ontology terms across OLS, optionally within one ontology.
- `query` (str), `ontology` (str, optional OLS id e.g. "go"), `limit` (int, optional).

### get_ontology_term
Term label, definition, synonyms. Ontology inferred from the OBO id prefix.
- `term_id` (str, e.g. "GO:0006915"), `ontology` (str, optional).

### get_go_annotations
Gene Ontology annotations for a gene/protein (QuickGO).
- `gene` (str, symbol or UniProt accession), `limit` (int, optional).

### get_uniprot_entries
UniProt protein entries by accession or search (human, reviewed).
- `query` (str, e.g. "TP53" or "P04637"), `limit` (int, optional).

### map_reactome_pathways
Reactome pathways a gene/protein participates in.
- `gene` (str, symbol or UniProt accession), `limit` (int, optional).

## Typical workflow

1. `query_genes` to get a gene's identifiers (Entrez/Ensembl/UniProt).
2. `get_uniprot_entries` for the protein; `get_go_annotations` for its GO terms;
   `map_reactome_pathways` for pathway membership.
3. `search_ontology_terms` / `get_ontology_term` to explore any ontology (GO, HP, …);
   `list_ontologies` to discover which are available.

## Notes

- Read-only; hits public MyGene / OLS / QuickGO / UniProt / Reactome endpoints, so
  responses depend on their uptime.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
