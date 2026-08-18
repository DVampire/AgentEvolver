---
name: structures_interactions_connector
description: Structures and molecular interactions — RCSB PDB structures, AlphaFold predictions, EMDB cryo-EM entries, Complex Portal complexes, IntAct interaction networks. Public APIs, no auth.
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
  - pdb_search_structures
  - pdb_get_structures
  - pdb_get_entities
  - pdb_get_ligands
  - alphafold_get_prediction
  - alphafold_check_coverage
  - emdb_search_entries
  - emdb_get_entries
  - emdb_get_entry_section
  - emdb_get_validation
  - complexportal_get_complexes
  - complexportal_search_by_participant
  - intact_fetch_interactions
  - intact_get_interactor
  - intact_get_interaction_details
  - intact_build_network
action_schemas:
  alphafold_check_coverage:
    properties:
      uniprot:
        title: Uniprot
        type: string
    required:
    - uniprot
    title: alphafold_check_coverageArguments
    type: object
  alphafold_get_prediction:
    properties:
      uniprot:
        title: Uniprot
        type: string
    required:
    - uniprot
    title: alphafold_get_predictionArguments
    type: object
  complexportal_get_complexes:
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
    title: complexportal_get_complexesArguments
    type: object
  complexportal_search_by_participant:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      participant:
        title: Participant
        type: string
    required:
    - participant
    title: complexportal_search_by_participantArguments
    type: object
  emdb_get_entries:
    properties:
      emdb_id:
        title: Emdb Id
        type: string
    required:
    - emdb_id
    title: emdb_get_entriesArguments
    type: object
  emdb_get_entry_section:
    properties:
      emdb_id:
        title: Emdb Id
        type: string
      section:
        default: sample
        title: Section
        type: string
    required:
    - emdb_id
    title: emdb_get_entry_sectionArguments
    type: object
  emdb_get_validation:
    properties:
      emdb_id:
        title: Emdb Id
        type: string
    required:
    - emdb_id
    title: emdb_get_validationArguments
    type: object
  emdb_search_entries:
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
    title: emdb_search_entriesArguments
    type: object
  intact_build_network:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: intact_build_networkArguments
    type: object
  intact_fetch_interactions:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: intact_fetch_interactionsArguments
    type: object
  intact_get_interaction_details:
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
    title: intact_get_interaction_detailsArguments
    type: object
  intact_get_interactor:
    properties:
      query:
        title: Query
        type: string
    required:
    - query
    title: intact_get_interactorArguments
    type: object
  pdb_get_entities:
    properties:
      pdb_id:
        title: Pdb Id
        type: string
    required:
    - pdb_id
    title: pdb_get_entitiesArguments
    type: object
  pdb_get_ligands:
    properties:
      pdb_id:
        title: Pdb Id
        type: string
    required:
    - pdb_id
    title: pdb_get_ligandsArguments
    type: object
  pdb_get_structures:
    properties:
      pdb_id:
        title: Pdb Id
        type: string
    required:
    - pdb_id
    title: pdb_get_structuresArguments
    type: object
  pdb_search_structures:
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
    title: pdb_search_structuresArguments
    type: object
action_descriptions:
  alphafold_check_coverage: "Check whether AlphaFold has a prediction for a UniProt\
    \ accession and its coverage.\n\n    Args:\n        uniprot: UniProt accession (e.g.\
    \ \"P04637\")."
  alphafold_get_prediction: "Get the AlphaFold predicted structure for a UniProt accession.\n\
    \n    Args:\n        uniprot: UniProt accession (e.g. \"P04637\")."
  complexportal_get_complexes: "Get curated protein complexes from Complex Portal by\
    \ name or complex accession.\n\n    Args:\n        query: complex name/keyword or\
    \ accession (e.g. \"GBAF\" or \"CPX-4084\").\n        limit: max complexes (default\
    \ 15)."
  complexportal_search_by_participant: "Find protein complexes containing a given participant\
    \ (protein/gene).\n\n    Args:\n        participant: protein name, gene, or UniProt\
    \ accession (e.g. \"CTCF\", \"P49711\").\n        limit: max complexes (default\
    \ 15)."
  emdb_get_entries: "Get an EMDB cryo-EM entry's summary (title, method, resolution).\n\
    \n    Args:\n        emdb_id: EMDB id (e.g. \"EMD-3489\")."
  emdb_get_entry_section: "Get one section of an EMDB entry's record as JSON-ish text.\n\
    \n    Args:\n        emdb_id: EMDB id (e.g. \"EMD-3489\").\n        section: one\
    \ of admin / sample / map / interpretation /\n            structure_determination_list\
    \ / crossreferences."
  emdb_get_validation: "Get validation-relevant info for an EMDB entry (resolution &\
    \ processing).\n\n    NOTE: EMDB serves full validation reports as PDFs; this summarizes\
    \ the processing\n    and resolution from the entry record.\n\n    Args:\n     \
    \   emdb_id: EMDB id (e.g. \"EMD-3489\")."
  emdb_search_entries: "Search EMDB cryo-EM entries by keyword.\n\n    Args:\n     \
    \   query: search text (e.g. \"ribosome\", \"spike\").\n        limit: max entries\
    \ (default 15)."
  intact_build_network: "Build an interaction network (nodes + edges) around a molecule\
    \ from IntAct.\n\n    Args:\n        query: gene/protein name or accession (e.g.\
    \ \"TP53\").\n        limit: max interactions to include (default 30)."
  intact_fetch_interactions: "Fetch molecular interactions for a molecule from IntAct.\n\
    \n    Args:\n        query: gene/protein name or accession (e.g. \"TP53\").\n  \
    \      limit: max interactions (default 20)."
  intact_get_interaction_details: "Get detailed evidence for interactions of a molecule\
    \ (method, type, publication).\n\n    Args:\n        query: gene/protein name or\
    \ accession (e.g. \"TP53\").\n        limit: max interactions (default 10)."
  intact_get_interactor: "Get interactor (molecule) info from IntAct.\n\n    Args:\n\
    \        query: gene/protein name or accession (e.g. \"TP53\")."
  pdb_get_entities: "List the polymer entities (chains) of a PDB structure.\n\n    Args:\n\
    \        pdb_id: PDB id (e.g. \"4HHB\")."
  pdb_get_ligands: "List the ligands (non-polymer entities) of a PDB structure.\n\n\
    \    Args:\n        pdb_id: PDB id (e.g. \"4HHB\")."
  pdb_get_structures: "Get an RCSB PDB structure's metadata (title, method, resolution,\
    \ organism).\n\n    Args:\n        pdb_id: 4-character PDB id (e.g. \"4HHB\")."
  pdb_search_structures: "Search RCSB PDB for experimental 3D structures by full-text\
    \ query.\n\n    Args:\n        query: search text (e.g. \"hemoglobin\", \"SARS-CoV-2\
    \ spike\").\n        limit: max structures (default 15)."
---
# Structures & Interactions

A self-contained MCP connector for macromolecular structures and molecular interactions,
over **public** APIs (no authentication): **RCSB PDB**, **AlphaFold**, **EMDB**,
**Complex Portal**, and **IntAct**. PDB/AlphaFold endpoint mappings referenced from the
open-source [cyanheads/protein-mcp-server](https://github.com/cyanheads/protein-mcp-server).

## Tools

### RCSB PDB (experimental structures)
- `pdb_search_structures` — full-text search. `pdb_get_structures` — entry metadata.
- `pdb_get_entities` — polymer chains. `pdb_get_ligands` — non-polymer ligands.

### AlphaFold (predicted structures)
- `alphafold_get_prediction` — model + mean pLDDT for a UniProt accession.
- `alphafold_check_coverage` — whether a prediction exists and its residue coverage.

### EMDB (cryo-EM)
- `emdb_search_entries` / `emdb_get_entries` (summary + resolution).
- `emdb_get_entry_section` — a named section of the record. `emdb_get_validation` — processing/resolution summary.

### Complex Portal (curated complexes)
- `complexportal_get_complexes` (by name/accession) / `complexportal_search_by_participant`.

### IntAct (interaction networks)
- `intact_fetch_interactions` / `intact_get_interactor` / `intact_get_interaction_details` /
  `intact_build_network` (nodes + edges).

## Typical workflow

1. `pdb_search_structures` → `pdb_get_structures` / `pdb_get_entities` / `pdb_get_ligands`
   for experimental structures; `alphafold_get_prediction` for a predicted model; `emdb_*` for cryo-EM maps.
2. `complexportal_search_by_participant` for a protein's complexes; `intact_fetch_interactions`
   / `intact_build_network` for its interaction network.

## Notes

- Read-only; hits public RCSB / AlphaFold / EMDB / Complex Portal / IntAct APIs, so responses
  depend on their uptime.
- `emdb_get_validation` summarizes processing/resolution (EMDB serves full validation reports as
  PDFs). The `connection` uses a relative `server.py` and `command: python`, which the connector manager resolves to absolute paths at load time, so no machine-specific paths are needed.
