---
name: drug_regulatory
description: FDA drug data — Drugs@FDA applications, approvals, pharmacologic classes, generic equivalents, SPL drug labels. Over the public openFDA API (no auth).
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: stdio
  command: /mnt/agent-framework/wentaozhang/miniconda3/envs/agentos/bin/python
  args:
    - /mnt/agent-framework/wentaozhang/AgentEvolver/src/connector/default/drug_regulatory/server.py
actions:
  - search_drug_applications
  - get_drug_application
  - count_drug_applications
  - get_drug_statistics
  - list_pharmacologic_classes
  - get_generic_equivalents
  - search_drug_labels
---

# Drug Regulatory

A self-contained MCP connector over the **public** openFDA drug API
(`https://api.fda.gov/drug`), no authentication. FDA regulatory data: Drugs@FDA
applications and approvals, pharmacologic classes, generic/brand equivalents, and
SPL drug labels.

## Tools

### search_drug_applications
Search Drugs@FDA by brand or generic name.
- `query` (str), `limit` (int, optional). → application, sponsor, brand, generic.

### get_drug_application
Details of one application (sponsor, products, first approval, ingredients).
- `application_number` (str, e.g. `NDA020702`, `ANDA207687`).

### count_drug_applications
Aggregate application counts grouped by an openFDA field.
- `field` (str, default `products.marketing_status`; e.g. `products.dosage_form.exact`),
  `search` (str, optional openFDA filter).

### get_drug_statistics
Approval/marketing statistics for a drug (by marketing status, dosage form, route).
- `drug` (str).

### list_pharmacologic_classes
Pharmacologic classes. With a drug → its EPC/MOA/PE/CS; without → most common EPC classes.
- `drug` (str, optional).

### get_generic_equivalents
Generic (ANDA) and brand (NDA) equivalents sharing a drug's active ingredient.
- `drug` (str), `limit` (int, optional).

### search_drug_labels
Search SPL drug labels; returns brand, indications snippet, SPL id.
- `query` (str), `limit` (int, optional).

## Typical workflow

1. `search_drug_applications` / `search_drug_labels` to find a drug and its FDA records.
2. `get_drug_application` for approval details; `list_pharmacologic_classes` for its class;
   `get_generic_equivalents` for available generics.
3. `count_drug_applications` / `get_drug_statistics` for regulatory-landscape aggregates.

## Notes

- Read-only; hits the public openFDA API (rate-limited without an API key), so responses
  depend on openFDA uptime.
- The `connection.command` / `args` above are absolute paths for this machine — update
  them if the repo or the Python environment moves.
