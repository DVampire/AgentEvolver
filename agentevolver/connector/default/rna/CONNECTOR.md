---
name: rna_connector
description: RNA families via Rfam — family metadata, seed alignments, covariance models, phylogenetic trees, PDB structure mappings, accession/id conversion, and sequence search. Public API, no auth.
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
  - get_family
  - get_seed_alignment
  - get_covariance_model
  - get_tree
  - get_sequence_regions
  - get_structure_mapping
  - accession_to_id
  - id_to_accession
  - search_sequence
action_schemas:
  accession_to_id:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: accession_to_idArguments
    type: object
  get_covariance_model:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: get_covariance_modelArguments
    type: object
  get_family:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: get_familyArguments
    type: object
  get_seed_alignment:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: get_seed_alignmentArguments
    type: object
  get_sequence_regions:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: get_sequence_regionsArguments
    type: object
  get_structure_mapping:
    properties:
      accession:
        title: Accession
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - accession
    title: get_structure_mappingArguments
    type: object
  get_tree:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: get_treeArguments
    type: object
  id_to_accession:
    properties:
      rfam_id:
        title: Rfam Id
        type: string
    required:
    - rfam_id
    title: id_to_accessionArguments
    type: object
  search_sequence:
    properties:
      max_wait:
        default: 25
        title: Max Wait
        type: integer
      sequence:
        title: Sequence
        type: string
    required:
    - sequence
    title: search_sequenceArguments
    type: object
action_descriptions:
  accession_to_id: "Convert an Rfam accession to its family id.\n\n    Args:\n     \
    \   accession: Rfam accession (e.g. \"RF00001\")."
  get_covariance_model: "Get an Rfam family's covariance model (CM) file header (truncated).\n\
    \n    Args:\n        accession: Rfam accession (e.g. \"RF00001\")."
  get_family: "Get an Rfam family's metadata (id, description, type, clan, curation).\n\
    \n    Args:\n        accession: Rfam accession (e.g. \"RF00001\") or family id (e.g.\
    \ \"5S_rRNA\")."
  get_seed_alignment: "Get an Rfam family's seed alignment (Stockholm format, truncated).\n\
    \n    Args:\n        accession: Rfam accession (e.g. \"RF00001\")."
  get_sequence_regions: "Sequence regions (genomic hits) of an Rfam family.\n\n    NOTE:\
    \ Rfam restricts the full-region web API; bulk regions are distributed via FTP.\n\
    \n    Args:\n        accession: Rfam accession (e.g. \"RF00001\")."
  get_structure_mapping: "Map an Rfam family to 3D structures (PDB) — CM-to-PDB region\
    \ mappings.\n\n    Args:\n        accession: Rfam accession (e.g. \"RF00001\").\n\
    \        limit: max mappings (default 25)."
  get_tree: "Get an Rfam family's phylogenetic tree (Newick, truncated).\n\n    Args:\n\
    \        accession: Rfam accession (e.g. \"RF00001\")."
  id_to_accession: "Convert an Rfam family id to its accession.\n\n    Args:\n     \
    \   rfam_id: Rfam family id (e.g. \"5S_rRNA\")."
  search_sequence: "Search a nucleotide sequence against Rfam covariance models (Infernal\
    \ cmscan).\n\n    Args:\n        sequence: RNA/DNA sequence (plain letters, no FASTA\
    \ header needed).\n        max_wait: seconds to wait for the async job (default\
    \ 25)."
---
# RNA

A self-contained MCP connector for RNA families over the **public** Rfam API
(`rfam.org`), no authentication. Rfam represents RNA families by multiple-sequence
alignments, consensus secondary structures, and covariance models.

## Tools

- `get_family` — family metadata (id, description, clan, curation). Args: `accession`.
- `get_seed_alignment` — seed alignment (Stockholm, truncated). Args: `accession`.
- `get_covariance_model` — covariance model (CM) header (truncated). Args: `accession`.
- `get_tree` — phylogenetic tree (Newick, truncated). Args: `accession`.
- `get_structure_mapping` — CM-to-PDB 3D structure mappings. Args: `accession`, `limit`.
- `accession_to_id` / `id_to_accession` — convert between accession (RF…) and family id.
- `get_sequence_regions` — genomic regions of a family (pointer to FTP; Rfam restricts the
  full-region web API).
- `search_sequence` — scan a nucleotide sequence against Rfam CMs (Infernal cmscan, async).

## Typical workflow

1. `get_family` (or `id_to_accession`) to identify a family; `get_structure_mapping` for its
   solved 3D structures.
2. `get_seed_alignment` / `get_covariance_model` / `get_tree` for the family's models.
3. `search_sequence` to classify an unknown RNA sequence into an Rfam family.

## Notes

- Read-only; hits the public Rfam API, so responses depend on its uptime.
- `get_sequence_regions` returns an FTP pointer (Rfam does not serve full regions over the web
  API); `search_sequence` uses Rfam's async cmscan service, which can be rate-limited (the tool
  degrades gracefully with a hint when it is unavailable).
- The `connection` uses a relative `server.py` and `command: python`, which the connector manager resolves to absolute paths at load time, so no machine-specific paths are needed.
