---
name: protein_annotation_connector
description: Protein annotation via EBI InterPro (InterPro entries, domain architecture, Pfam clans & families), the Human Protein Atlas (per-gene records, tissue/subcellular search), and STRING (protein-protein interaction networks, homology). Public APIs, no auth.
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
  - search_interpro_entries
  - get_interpro_entry
  - get_domain_architecture
  - search_pfam_clans
  - get_pfam_clan
  - get_pfam_family_proteins
  - get_pfam_family_proteomes
  - get_protein_atlas_gene
  - search_protein_atlas
  - map_string_ids
  - get_string_network
  - get_string_similarity_scores
  - get_string_best_similarity_hits
action_schemas:
  get_domain_architecture:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
      uniprot:
        title: Uniprot
        type: string
    required:
    - uniprot
    title: get_domain_architectureArguments
    type: object
  get_interpro_entry:
    properties:
      interpro_id:
        title: Interpro Id
        type: string
    required:
    - interpro_id
    title: get_interpro_entryArguments
    type: object
  get_pfam_clan:
    properties:
      clan_id:
        title: Clan Id
        type: string
    required:
    - clan_id
    title: get_pfam_clanArguments
    type: object
  get_pfam_family_proteins:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      pfam_id:
        title: Pfam Id
        type: string
    required:
    - pfam_id
    title: get_pfam_family_proteinsArguments
    type: object
  get_pfam_family_proteomes:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      pfam_id:
        title: Pfam Id
        type: string
    required:
    - pfam_id
    title: get_pfam_family_proteomesArguments
    type: object
  get_protein_atlas_gene:
    properties:
      gene:
        title: Gene
        type: string
    required:
    - gene
    title: get_protein_atlas_geneArguments
    type: object
  get_string_best_similarity_hits:
    properties:
      genes:
        items:
          type: string
        title: Genes
        type: array
      species:
        default: 9606
        title: Species
        type: integer
      species_b:
        default: ''
        title: Species B
        type: string
    required:
    - genes
    title: get_string_best_similarity_hitsArguments
    type: object
  get_string_network:
    properties:
      genes:
        items:
          type: string
        title: Genes
        type: array
      required_score:
        default: 400
        title: Required Score
        type: integer
      species:
        default: 9606
        title: Species
        type: integer
    required:
    - genes
    title: get_string_networkArguments
    type: object
  get_string_similarity_scores:
    properties:
      genes:
        items:
          type: string
        title: Genes
        type: array
      species:
        default: 9606
        title: Species
        type: integer
    required:
    - genes
    title: get_string_similarity_scoresArguments
    type: object
  map_string_ids:
    properties:
      genes:
        items:
          type: string
        title: Genes
        type: array
      species:
        default: 9606
        title: Species
        type: integer
    required:
    - genes
    title: map_string_idsArguments
    type: object
  search_interpro_entries:
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
    title: search_interpro_entriesArguments
    type: object
  search_pfam_clans:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      query:
        default: ''
        title: Query
        type: string
    title: search_pfam_clansArguments
    type: object
  search_protein_atlas:
    properties:
      columns:
        default: ''
        title: Columns
        type: string
      limit:
        default: 15
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: search_protein_atlasArguments
    type: object
action_descriptions:
  get_domain_architecture: "Get the domain architecture of a protein — all InterPro/member\
    \ entries on it.\n\n    Args:\n        uniprot: UniProt accession (e.g. \"P04637\"\
    ).\n        limit: max entries (default 30)."
  get_interpro_entry: "Get an InterPro entry's details (type, GO terms, member DBs,\
    \ description).\n\n    Args:\n        interpro_id: InterPro accession (e.g. \"IPR000719\"\
    )."
  get_pfam_clan: "Get a Pfam clan's details (name, description).\n\n    Args:\n    \
    \    clan_id: Pfam clan accession (e.g. \"CL0001\")."
  get_pfam_family_proteins: "List UniProt proteins that contain a given Pfam family.\n\
    \n    Args:\n        pfam_id: Pfam family accession (e.g. \"PF00069\").\n      \
    \  limit: max proteins (default 20)."
  get_pfam_family_proteomes: "List proteomes (organisms) in which a Pfam family is found.\n\
    \n    Args:\n        pfam_id: Pfam family accession (e.g. \"PF00069\").\n      \
    \  limit: max proteomes (default 20)."
  get_protein_atlas_gene: "Get the Human Protein Atlas record for a single gene.\n\n\
    \    Args:\n        gene: Ensembl gene id (e.g. \"ENSG00000141510\") or gene symbol\
    \ (e.g. \"TP53\").\n            A symbol is resolved to its Ensembl id via the HPA\
    \ search API first.\n    Returns key HPA fields: identity, protein class, RNA/protein\
    \ tissue specificity,\n    subcellular location, and pathology/prognostic summaries\
    \ when present."
  get_string_best_similarity_hits: "Get each protein's best homolog (highest bit-score)\
    \ in target species via STRING.\n\n    Args:\n        genes: list of gene symbols/identifiers\
    \ in the source species.\n        species: source NCBI taxon id (default 9606 =\
    \ human).\n        species_b: optional comma-separated target taxon id(s) to restrict\
    \ hits to\n            (e.g. \"10090\" for mouse); empty = best hit across all STRING\
    \ species.\n    Returns 'sourceProtein<TAB>targetTaxon<TAB>targetProtein<TAB>bitscore'\
    \ rows."
  get_string_network: "Get the STRING protein-protein interaction network for a set\
    \ of genes.\n\n    Args:\n        genes: list of gene symbols/identifiers (e.g.\
    \ [\"TP53\", \"MDM2\", \"CDKN1A\"]).\n        species: NCBI taxon id (default 9606\
    \ = human).\n        required_score: minimum combined STRING score, 0-1000 (default\
    \ 400 = medium confidence).\n    Returns interacting pairs with their combined and\
    \ channel-specific scores."
  get_string_similarity_scores: "Get STRING homology (Smith-Waterman bit-score) similarities\
    \ among a set of proteins.\n\n    Args:\n        genes: list of gene symbols/identifiers\
    \ (e.g. [\"TP53\", \"TP63\", \"TP73\"]).\n        species: NCBI taxon id (default\
    \ 9606 = human).\n    Returns all-vs-all bit scores between the input proteins."
  map_string_ids: "Map gene symbols/identifiers to STRING protein identifiers.\n\n \
    \   Args:\n        genes: list of gene symbols or identifiers (e.g. [\"TP53\", \"\
    EGFR\", \"MDM2\"]).\n        species: NCBI taxon id (default 9606 = human).\n  \
    \  Returns 'input<TAB>stringId<TAB>preferredName<TAB>annotation' rows."
  search_interpro_entries: "Search InterPro entries (domains/families/sites) by keyword.\n\
    \n    Args:\n        query: search text (e.g. \"kinase\", \"zinc finger\").\n  \
    \      limit: max entries (default 15).\n    Returns 'accession<TAB>name<TAB>type'\
    \ rows."
  search_pfam_clans: "Search/list Pfam clans (superfamilies grouping related families).\n\
    \n    Args:\n        query: substring to match against clan accession/name (optional).\n\
    \        limit: max clans (default 20)."
  search_protein_atlas: "Search the Human Protein Atlas and download selected columns\
    \ as rows.\n\n    Args:\n        query: HPA search query — a gene symbol/name, or\
    \ a field query such as\n            \"protein_class:Transcription factors\" or\
    \ \"tissue_category_rna:liver;Tissue enriched\".\n        columns: comma-separated\
    \ HPA column codes (default gene identity + specificity/location:\n            \"\
    g,gs,eg,gd,up,chr,pc,rnats,scl\"). See proteinatlas.org for the full code list.\n\
    \        limit: max rows (default 15)."
---
# Protein Annotation

A self-contained MCP connector for protein domains, families, tissue expression and
interaction networks over three **public** APIs (no authentication):

- **EBI InterPro** (`www.ebi.ac.uk/interpro/api`) — InterPro entries and Pfam
  clans/families (Pfam is integrated into InterPro).
- **Human Protein Atlas** (`www.proteinatlas.org`) — per-gene records and search over
  tissue/subcellular expression columns.
- **STRING** (`string-db.org`) — protein-protein interaction networks and homology.

## Tools

### InterPro / Pfam
- `search_interpro_entries` — search entries (domains/families/sites) by keyword. Args: `query`, `limit`.
- `get_interpro_entry` — entry details: type, GO terms, member DBs, description. Args: `interpro_id`.
- `get_domain_architecture` — all InterPro/member entries on a protein. Args: `uniprot`, `limit`.
- `search_pfam_clans` — search/list Pfam clans (superfamilies). Args: `query`, `limit`.
- `get_pfam_clan` — clan details. Args: `clan_id` (e.g. "CL0001").
- `get_pfam_family_proteins` — UniProt proteins containing a Pfam family. Args: `pfam_id` (e.g. "PF00069"), `limit`.
- `get_pfam_family_proteomes` — proteomes (organisms) containing a Pfam family. Args: `pfam_id`, `limit`.

### Human Protein Atlas
- `get_protein_atlas_gene` — single-gene HPA record (identity, protein class, RNA/protein tissue specificity, subcellular location). Args: `gene` (Ensembl id or symbol).
- `search_protein_atlas` — search HPA and download selected columns. Args: `query`, `columns` (HPA column codes), `limit`.

### STRING
- `map_string_ids` — map gene symbols to STRING protein ids. Args: `genes` (list), `species`.
- `get_string_network` — interaction network for a gene set. Args: `genes` (list), `species`, `required_score`.
- `get_string_similarity_scores` — all-vs-all homology bit-scores within a gene set. Args: `genes` (list), `species`.
- `get_string_best_similarity_hits` — each protein's best homolog in target species. Args: `genes` (list), `species`, `species_b`.

## Typical workflow

1. `search_interpro_entries` → `get_interpro_entry`; `get_domain_architecture` for a specific protein.
2. `search_pfam_clans` / `get_pfam_clan`; `get_pfam_family_proteins` / `get_pfam_family_proteomes`.
3. `get_protein_atlas_gene` / `search_protein_atlas` for tissue expression and localization.
4. `map_string_ids` → `get_string_network` for interactions; `get_string_similarity_scores` /
   `get_string_best_similarity_hits` for within-set and cross-species homology.

## Notes

- Read-only; hits the public InterPro, Human Protein Atlas and STRING APIs, so responses
  depend on their uptime. STRING uses POST with a `caller_identity`; HPA search uses its
  `search_download.php` column codes (see proteinatlas.org for the full list).
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
