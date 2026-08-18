---
name: biomart_connector
description: Ensembl BioMart — genomic annotations, identifier translation, and cross-reference queries over the public Ensembl BioMart REST API (no auth).
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
  - list_marts
  - list_datasets
  - list_common_attributes
  - list_all_attributes
  - list_filters
  - get_data
  - get_translation
  - batch_translate
action_schemas:
  batch_translate:
    properties:
      dataset:
        title: Dataset
        type: string
      from_attribute:
        title: From Attribute
        type: string
      to_attribute:
        title: To Attribute
        type: string
      values:
        items:
          type: string
        title: Values
        type: array
    required:
    - dataset
    - from_attribute
    - to_attribute
    - values
    title: batch_translateArguments
    type: object
  get_data:
    properties:
      attributes:
        items:
          type: string
        title: Attributes
        type: array
      dataset:
        title: Dataset
        type: string
      filters:
        anyOf:
        - additionalProperties: true
          type: object
        - type: 'null'
        default: null
        title: Filters
    required:
    - dataset
    - attributes
    title: get_dataArguments
    type: object
  get_translation:
    properties:
      dataset:
        title: Dataset
        type: string
      from_attribute:
        title: From Attribute
        type: string
      to_attribute:
        title: To Attribute
        type: string
      value:
        title: Value
        type: string
    required:
    - dataset
    - from_attribute
    - to_attribute
    - value
    title: get_translationArguments
    type: object
  list_all_attributes:
    properties:
      dataset:
        default: hsapiens_gene_ensembl
        title: Dataset
        type: string
      search:
        default: ''
        title: Search
        type: string
    title: list_all_attributesArguments
    type: object
  list_common_attributes:
    properties:
      dataset:
        default: hsapiens_gene_ensembl
        title: Dataset
        type: string
    title: list_common_attributesArguments
    type: object
  list_datasets:
    properties:
      mart:
        default: ENSEMBL_MART_ENSEMBL
        title: Mart
        type: string
    title: list_datasetsArguments
    type: object
  list_filters:
    properties:
      dataset:
        default: hsapiens_gene_ensembl
        title: Dataset
        type: string
      search:
        default: ''
        title: Search
        type: string
    title: list_filtersArguments
    type: object
  list_marts:
    properties: {}
    title: list_martsArguments
    type: object
action_descriptions:
  batch_translate: |-
    Translate many identifiers at once between two attribute types.

    Args:
        dataset: e.g. hsapiens_gene_ensembl.
        from_attribute: source ID type (e.g. "hgnc_symbol").
        to_attribute: target ID type (e.g. "entrezgene_id").
        values: list of identifiers, e.g. ["TP53", "BRCA1", "EGFR"].
  get_data: |-
    Run a BioMart query: fetch `attributes` from `dataset`, constrained by `filters`.

    This is the main data-retrieval tool. Returns TSV with a header row.

    Args:
        dataset: e.g. hsapiens_gene_ensembl (see list_datasets).
        attributes: attribute names to return, e.g. ["ensembl_gene_id", "hgnc_symbol"].
        filters: optional {filter_name: value}; value may be comma-separated for lists,
            e.g. {"hgnc_symbol": "TP53,BRCA1"} or {"chromosome_name": "17"}.
  get_translation: |-
    Translate a single identifier from one attribute type to another.

    Args:
        dataset: e.g. hsapiens_gene_ensembl.
        from_attribute: source ID type, usable as both filter and attribute
            (e.g. "hgnc_symbol", "ensembl_gene_id", "entrezgene_id").
        to_attribute: target ID type (e.g. "ensembl_gene_id").
        value: the identifier to translate (e.g. "TP53").
  list_all_attributes: |-
    List all attributes for a dataset, optionally filtered by a search substring.

    The full list can be thousands of entries — pass `search` (matched against the
    attribute name and description) to narrow it.

    Args:
        dataset: e.g. hsapiens_gene_ensembl.
        search: case-insensitive substring filter (optional).
  list_common_attributes: |-
    List the most commonly used attributes (fields) for a gene dataset.

    A curated shortlist for the frequent case; use list_all_attributes to search
    the full attribute set.

    Args:
        dataset: e.g. hsapiens_gene_ensembl.
  list_datasets: |-
    List datasets in a mart (e.g. hsapiens_gene_ensembl for human genes).

    Args:
        mart: Mart name from list_marts (default ENSEMBL_MART_ENSEMBL).
    Returns 'dataset<TAB>description' rows.
  list_filters: |-
    List the filters available to constrain a query on a dataset.

    Args:
        dataset: e.g. hsapiens_gene_ensembl.
        search: case-insensitive substring filter (optional).
  list_marts: |-
    List the available BioMart marts (databases), e.g. ENSEMBL_MART_ENSEMBL.

    Use this first to discover which mart to query. Returns 'name<TAB>displayName'.
---
# BioMart

A self-contained MCP connector over the **public** Ensembl BioMart REST API
(`martservice`). No authentication and no proprietary endpoints — it wraps
`https://<mirror>.ensembl.org/biomart/martservice`, trying regional mirrors
(useast first) for reliability. Supports genomic annotation lookups, identifier
translation, and cross-reference queries across species.

## Tools

### list_marts
List available marts (databases), e.g. `ENSEMBL_MART_ENSEMBL`. Start here.
- No arguments. Returns `name<TAB>displayName`.

### list_datasets
List datasets in a mart (e.g. `hsapiens_gene_ensembl` for human genes).
- `mart` (str, optional, default `ENSEMBL_MART_ENSEMBL`).

### list_common_attributes
Curated shortlist of the most-used attributes for a gene dataset (gene id, symbol,
Entrez, UniProt, chromosome, coordinates, biotype, description).
- `dataset` (str, optional, default `hsapiens_gene_ensembl`).

### list_all_attributes
Full attribute list for a dataset (can be thousands — filter with `search`).
- `dataset` (str, optional), `search` (str, optional substring).

### list_filters
Filters available to constrain a query on a dataset.
- `dataset` (str, optional), `search` (str, optional substring).

### get_data
Main query: fetch `attributes` from `dataset`, constrained by `filters`. Returns TSV.
- `dataset` (str), `attributes` (list[str]), `filters` (dict, optional; values may be
  comma-separated, e.g. `{"hgnc_symbol": "TP53,BRCA1"}` or `{"chromosome_name": "17"}`).

### get_translation
Translate a single identifier between two attribute types.
- `dataset` (str), `from_attribute` (str), `to_attribute` (str), `value` (str).

### batch_translate
Translate many identifiers at once between two attribute types.
- `dataset` (str), `from_attribute` (str), `to_attribute` (str), `values` (list[str]).

## Typical workflow

1. `list_marts` → `list_datasets` to locate the mart/dataset (e.g. human genes).
2. `list_common_attributes` / `list_all_attributes(search=...)` and `list_filters(search=...)`
   to discover the field and filter names you need.
3. `get_data` for the actual annotation query, or `get_translation` / `batch_translate`
   for ID cross-referencing (e.g. HGNC symbol → Ensembl gene ID / Entrez ID).

## Notes

- Read-only; queries hit public Ensembl mirrors, so responses depend on Ensembl uptime.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
