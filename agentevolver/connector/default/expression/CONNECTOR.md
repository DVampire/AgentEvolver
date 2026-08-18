---
name: expression_connector
description: Gene expression — GTEx tissue expression and eQTLs (pinned gtex_v10 dataset). Over the public GTEx Portal API (no auth).
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
  - gtex_tissue_sites
  - gtex_dataset_info
  - gtex_sample_info
  - gtex_resolve_genes
  - gtex_median_expression
  - gtex_expression_summary
  - gtex_gene_expression
  - gtex_top_expressed_genes
  - gtex_eqtl_genes
  - gtex_single_tissue_eqtls
  - gtex_multi_tissue_eqtls
  - gtex_calculate_eqtl
action_schemas:
  gtex_calculate_eqtl:
    properties:
      gene:
        title: Gene
        type: string
      tissue:
        title: Tissue
        type: string
      variant:
        title: Variant
        type: string
    required:
    - gene
    - variant
    - tissue
    title: gtex_calculate_eqtlArguments
    type: object
  gtex_dataset_info:
    properties: {}
    title: gtex_dataset_infoArguments
    type: object
  gtex_eqtl_genes:
    properties:
      limit:
        default: 25
        title: Limit
        type: integer
      tissue:
        title: Tissue
        type: string
    required:
    - tissue
    title: gtex_eqtl_genesArguments
    type: object
  gtex_expression_summary:
    properties:
      gene:
        title: Gene
        type: string
      top:
        default: 8
        title: Top
        type: integer
    required:
    - gene
    title: gtex_expression_summaryArguments
    type: object
  gtex_gene_expression:
    properties:
      gene:
        title: Gene
        type: string
      tissue:
        title: Tissue
        type: string
    required:
    - gene
    - tissue
    title: gtex_gene_expressionArguments
    type: object
  gtex_median_expression:
    properties:
      gene:
        title: Gene
        type: string
    required:
    - gene
    title: gtex_median_expressionArguments
    type: object
  gtex_multi_tissue_eqtls:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 40
        title: Limit
        type: integer
    required:
    - gene
    title: gtex_multi_tissue_eqtlsArguments
    type: object
  gtex_resolve_genes:
    properties:
      gene:
        title: Gene
        type: string
    required:
    - gene
    title: gtex_resolve_genesArguments
    type: object
  gtex_sample_info:
    properties:
      limit:
        default: 10
        title: Limit
        type: integer
      tissue:
        default: ''
        title: Tissue
        type: string
    title: gtex_sample_infoArguments
    type: object
  gtex_single_tissue_eqtls:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
      tissue:
        title: Tissue
        type: string
    required:
    - gene
    - tissue
    title: gtex_single_tissue_eqtlsArguments
    type: object
  gtex_tissue_sites:
    properties: {}
    title: gtex_tissue_sitesArguments
    type: object
  gtex_top_expressed_genes:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      tissue:
        title: Tissue
        type: string
    required:
    - tissue
    title: gtex_top_expressed_genesArguments
    type: object
action_descriptions:
  gtex_calculate_eqtl: "Compute a single-tissue cis-eQTL for an arbitrary gene-variant\
    \ pair on the fly.\n\n    Runs GTEx's dynamic eQTL calculation (dyneqtl): regresses\
    \ the gene's expression\n    on the variant's genotype in one tissue and returns\
    \ the association statistics,\n    even for pairs not in the pre-computed significant-eQTL\
    \ tables.\n\n    Args:\n        gene: gene symbol or gencodeId (e.g. \"TP53\").\n\
    \        variant: GTEx variantId (e.g. \"chr17_7676154_G_C_b38\") or dbSNP rsID\
    \ (resolved automatically).\n        tissue: tissueSiteDetailId (e.g. \"Whole_Blood\"\
    )."
  gtex_dataset_info: Describe the available GTEx datasets (id, samples, subjects, GENCODE
    version).
  gtex_eqtl_genes: "List eGenes (genes with a significant cis-eQTL) in a tissue.\n\n\
    \    Args:\n        tissue: tissueSiteDetailId (e.g. \"Whole_Blood\").\n       \
    \ limit: max eGenes (default 25)."
  gtex_expression_summary: "Concise expression summary for a gene: highest- and lowest-expressing\
    \ tissues.\n\n    Args:\n        gene: gene symbol or gencodeId.\n        top: how\
    \ many top/bottom tissues to show (default 8)."
  gtex_gene_expression: "Per-sample expression distribution of a gene in one tissue\
    \ (summary stats).\n\n    Args:\n        gene: gene symbol or gencodeId.\n     \
    \   tissue: tissueSiteDetailId (e.g. \"Liver\")."
  gtex_median_expression: "Median expression (TPM) of a gene across all GTEx tissues,\
    \ highest first.\n\n    Args:\n        gene: gene symbol or gencodeId (e.g. \"TP53\"\
    )."
  gtex_multi_tissue_eqtls: "Multi-tissue eQTL meta-analysis for a gene.\n\n    Attempts\
    \ GTEx's Metasoft cross-tissue meta-analysis (per-tissue m-value/p-value).\n   \
    \ Metasoft is not populated for every gene in the v2 API, so when it is empty this\n\
    \    falls back to aggregating the gene's significant single-tissue cis-eQTLs across\
    \ all\n    tissues, grouped per variant: how many tissues each lead variant is significant\
    \ in,\n    its best p-value, and its effect-size range — a practical multi-tissue\
    \ summary.\n\n    Args:\n        gene: gene symbol or gencodeId (e.g. \"ERAP2\"\
    ).\n        limit: max variants/rows to return (default 40)."
  gtex_resolve_genes: "Resolve a gene symbol to its GTEx gencodeId (and basic annotation).\n\
    \n    Args:\n        gene: gene symbol (e.g. \"TP53\") or Ensembl id."
  gtex_sample_info: "Sample metadata for the dataset, optionally filtered by tissue.\n\
    \n    Args:\n        tissue: tissueSiteDetailId (e.g. \"Liver\"); empty for all.\n\
    \        limit: max sample rows to show (default 10)."
  gtex_single_tissue_eqtls: "Single-tissue cis-eQTLs for a gene in a tissue (variant,\
    \ p-value, effect size).\n\n    Args:\n        gene: gene symbol or gencodeId.\n\
    \        tissue: tissueSiteDetailId (e.g. \"Whole_Blood\").\n        limit: max\
    \ eQTLs (default 25)."
  gtex_tissue_sites: List GTEx tissue sites (their ids and display names). Use ids for
    other tools.
  gtex_top_expressed_genes: "Top-expressed genes in a tissue by median TPM.\n\n    Args:\n\
    \        tissue: tissueSiteDetailId (e.g. \"Liver\").\n        limit: max genes\
    \ (default 20)."
---
# Expression (GTEx)

A self-contained MCP connector over the **public** GTEx Portal API
(`https://gtexportal.org/api/v2`), no authentication. Human tissue gene expression
and cis-eQTLs, pinned to the **gtex_v10** dataset (GENCODE v39 identifiers, resolved
automatically from gene symbols).

## Tools

### gtex_tissue_sites
List tissue sites (ids + display names). Use the ids for other tools. No args.

### gtex_dataset_info
Describe available GTEx datasets (samples, subjects, GENCODE version). No args.

### gtex_sample_info
Sample metadata, optionally filtered by tissue.
- `tissue` (str, optional tissueSiteDetailId), `limit` (int, optional).

### gtex_resolve_genes
Resolve a gene symbol to its GTEx gencodeId + annotation.
- `gene` (str, e.g. "TP53").

### gtex_median_expression
Median TPM of a gene across all tissues, highest first.
- `gene` (str).

### gtex_expression_summary
Concise summary: highest/lowest tissues + median-of-medians for a gene.
- `gene` (str), `top` (int, optional).

### gtex_gene_expression
Per-sample expression distribution of a gene in one tissue (summary stats).
- `gene` (str), `tissue` (str tissueSiteDetailId).

### gtex_top_expressed_genes
Top-expressed genes in a tissue by median TPM.
- `tissue` (str), `limit` (int, optional).

### gtex_eqtl_genes
eGenes (genes with a significant cis-eQTL) in a tissue.
- `tissue` (str), `limit` (int, optional).

### gtex_single_tissue_eqtls
Single-tissue cis-eQTLs for a gene in a tissue (variant, p-value, effect size).
- `gene` (str), `tissue` (str), `limit` (int, optional).

### gtex_multi_tissue_eqtls
Multi-tissue eQTL meta-analysis (Metasoft) for a gene: per variant×tissue m-value
(posterior probability of effect) and p-value.
- `gene` (str), `limit` (int, optional).

### gtex_calculate_eqtl
Compute a single-tissue cis-eQTL for any gene-variant pair on the fly (dynEqtl),
even for pairs absent from the pre-computed significant-eQTL tables.
- `gene` (str), `variant` (str, GTEx variantId or rsID), `tissue` (str tissueSiteDetailId).

## Typical workflow

1. `gtex_tissue_sites` to get tissue ids; `gtex_resolve_genes` for a gene's gencodeId.
2. `gtex_median_expression` / `gtex_expression_summary` for where a gene is expressed;
   `gtex_gene_expression` for one tissue's distribution.
3. `gtex_top_expressed_genes` for a tissue's markers.
4. `gtex_eqtl_genes` / `gtex_single_tissue_eqtls` for regulatory (eQTL) variants;
   `gtex_multi_tissue_eqtls` for the cross-tissue meta-analysis, and
   `gtex_calculate_eqtl` to test an arbitrary gene-variant pair directly.

## Notes

- Read-only; hits the public GTEx Portal API, so responses depend on its uptime.
- Pinned to gtex_v10 (GENCODE v39); gene symbols are resolved to matching gencodeIds
  automatically. The `connection` uses a relative `server.py` and `command: python`, which the connector manager resolves to absolute paths at load time, so no machine-specific paths are needed.
