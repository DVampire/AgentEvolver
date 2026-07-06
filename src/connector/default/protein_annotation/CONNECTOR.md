---
name: protein_annotation
description: Protein annotation via EBI InterPro — InterPro entries (domains/families/sites), protein domain architecture, and Pfam clans & families. Public API, no auth.
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: stdio
  command: /mnt/agent-framework/wentaozhang/miniconda3/envs/agentos/bin/python
  args:
    - /mnt/agent-framework/wentaozhang/AgentEvolver/src/connector/default/protein_annotation/server.py
actions:
  - search_interpro_entries
  - get_interpro_entry
  - get_domain_architecture
  - search_pfam_clans
  - get_pfam_clan
  - get_pfam_family_proteins
  - get_pfam_family_proteomes
---

# Protein Annotation

A self-contained MCP connector for protein domains and families over the **public**
EBI **InterPro** API (`www.ebi.ac.uk/interpro/api`), no authentication. Pfam is
integrated into InterPro, so both InterPro entries and Pfam clans/families are covered.

## Tools

- `search_interpro_entries` — search entries (domains/families/sites) by keyword. Args: `query`, `limit`.
- `get_interpro_entry` — entry details: type, GO terms, member DBs, description. Args: `interpro_id`.
- `get_domain_architecture` — all InterPro/member entries on a protein. Args: `uniprot`, `limit`.
- `search_pfam_clans` — search/list Pfam clans (superfamilies). Args: `query`, `limit`.
- `get_pfam_clan` — clan details. Args: `clan_id` (e.g. "CL0001").
- `get_pfam_family_proteins` — UniProt proteins containing a Pfam family. Args: `pfam_id` (e.g. "PF00069"), `limit`.
- `get_pfam_family_proteomes` — proteomes (organisms) containing a Pfam family. Args: `pfam_id`, `limit`.

## Typical workflow

1. `search_interpro_entries` to find a domain/family; `get_interpro_entry` for its GO terms
   and member databases.
2. `get_domain_architecture` for the domains on a specific protein (by UniProt accession).
3. `search_pfam_clans` / `get_pfam_clan` for superfamilies; `get_pfam_family_proteins` /
   `get_pfam_family_proteomes` to see where a Pfam family occurs.

## Notes

- Read-only; hits the public EBI InterPro API, so responses depend on its uptime.
  (STRING interaction networks and Human Protein Atlas were intentionally excluded — their
  hosts are not reachable from this build environment; they can be added separately where
  reachable.)
- The `connection.command` / `args` above are absolute paths for this machine — update
  them if the repo or the Python environment moves.
