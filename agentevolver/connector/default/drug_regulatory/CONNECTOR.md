---
name: drug_regulatory_connector
description: FDA drug data — Drugs@FDA applications, approvals, pharmacologic classes, generic equivalents, SPL drug labels. Over the public openFDA API (no auth).
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
  - search_drug_applications
  - get_drug_application
  - count_drug_applications
  - get_drug_statistics
  - list_pharmacologic_classes
  - get_generic_equivalents
  - search_drug_labels
action_schemas:
  count_drug_applications:
    properties:
      field:
        default: products.marketing_status
        title: Field
        type: string
      search:
        default: ''
        title: Search
        type: string
    title: count_drug_applicationsArguments
    type: object
  get_drug_application:
    properties:
      application_number:
        title: Application Number
        type: string
    required:
    - application_number
    title: get_drug_applicationArguments
    type: object
  get_drug_statistics:
    properties:
      drug:
        title: Drug
        type: string
    required:
    - drug
    title: get_drug_statisticsArguments
    type: object
  get_generic_equivalents:
    properties:
      drug:
        title: Drug
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - drug
    title: get_generic_equivalentsArguments
    type: object
  list_pharmacologic_classes:
    properties:
      drug:
        default: ''
        title: Drug
        type: string
    title: list_pharmacologic_classesArguments
    type: object
  search_drug_applications:
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
    title: search_drug_applicationsArguments
    type: object
  search_drug_labels:
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
    title: search_drug_labelsArguments
    type: object
action_descriptions:
  count_drug_applications: |-
    Aggregate a count of Drugs@FDA applications grouped by a field.

    Args:
        field: openFDA field to group by (e.g. "products.marketing_status",
            "products.dosage_form.exact", "products.route.exact").
        search: optional openFDA search filter (e.g. 'openfda.generic_name:"aspirin"').
  get_drug_application: |-
    Get details of one Drugs@FDA application (sponsor, products, approval).

    Args:
        application_number: e.g. "NDA020702" or "ANDA207687".
  get_drug_statistics: |-
    Approval/marketing statistics for a drug across Drugs@FDA (by status & dosage form).

    Args:
        drug: generic or brand name (e.g. "atorvastatin").
  get_generic_equivalents: |-
    Find generic (ANDA) and brand (NDA) equivalents sharing a drug's active ingredient.

    Args:
        drug: generic or brand name (e.g. "Lipitor", "atorvastatin").
        limit: max applications (default 25).
  list_pharmacologic_classes: |-
    List pharmacologic classes (EPC/MOA/PE/CS). For a drug, its classes; else top EPC classes.

    Args:
        drug: optional drug name; if empty, returns the most common EPC classes overall.
  search_drug_applications: |-
    Search Drugs@FDA applications by brand or generic drug name.

    Args:
        query: brand or generic name (e.g. "atorvastatin", "Lipitor").
        limit: max applications (default 10).
    Returns 'application<TAB>sponsor<TAB>brand<TAB>generic' rows.
  search_drug_labels: |-
    Search SPL drug labels; returns brand, indications snippet, and SPL id.

    Args:
        query: brand or generic name (e.g. "ibuprofen").
        limit: max labels (default 5).
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
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
