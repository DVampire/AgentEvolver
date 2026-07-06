---
name: genomes
description: Genome annotation via Ensembl REST — gene lookup, cross-references, variant effect prediction (VEP), homology/orthologues, sequence retrieval, and region overlap. Public API, no auth.
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: stdio
  command: /mnt/agent-framework/wentaozhang/miniconda3/envs/agentos/bin/python
  args:
    - /mnt/agent-framework/wentaozhang/AgentEvolver/src/connector/default/genomes/server.py
actions:
  - ensembl_lookup
  - ensembl_xrefs
  - ensembl_vep_variant
  - ensembl_homology
  - ensembl_sequence
  - ensembl_overlap_region
---

# Genomes

A self-contained MCP connector for genome annotation over the **public** Ensembl REST
API (`rest.ensembl.org`), no authentication.

## Tools

- `ensembl_lookup` — gene/transcript by Ensembl id or symbol. Args: `query`, `species`.
- `ensembl_xrefs` — external DB cross-references for a gene. Args: `query`, `species`.
- `ensembl_vep_variant` — variant effect prediction for an rsID / HGVS. Args: `variant`, `species`.
- `ensembl_homology` — orthologues across species. Args: `gene`, `species`, `target_species`.
- `ensembl_sequence` — sequence for an id (genomic/cds/cdna/protein). Args: `ensembl_id`, `seq_type`, `max_len`.
- `ensembl_overlap_region` — features overlapping a region. Args: `region` ("chr:start-end"), `species`, `feature`.

## Typical workflow

1. `ensembl_lookup` for a gene's coordinates/id; `ensembl_xrefs` for external ids.
2. `ensembl_overlap_region` for what else is in a locus; `ensembl_sequence` for sequence.
3. `ensembl_vep_variant` for a variant's effect; `ensembl_homology` for orthologues.

## Notes

- Read-only; hits the public Ensembl REST API, so responses depend on its uptime.
- The `connection.command` / `args` above are absolute paths for this machine — update
  them if the repo or the Python environment moves.
