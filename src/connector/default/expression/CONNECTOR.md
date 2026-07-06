---
name: expression
description: Gene expression — GTEx tissue expression and eQTLs (pinned gtex_v10 dataset). Over the public GTEx Portal API (no auth).
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: stdio
  command: /mnt/agent-framework/wentaozhang/miniconda3/envs/agentos/bin/python
  args:
    - /mnt/agent-framework/wentaozhang/AgentEvolver/src/connector/default/expression/server.py
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

## Typical workflow

1. `gtex_tissue_sites` to get tissue ids; `gtex_resolve_genes` for a gene's gencodeId.
2. `gtex_median_expression` / `gtex_expression_summary` for where a gene is expressed;
   `gtex_gene_expression` for one tissue's distribution.
3. `gtex_top_expressed_genes` for a tissue's markers.
4. `gtex_eqtl_genes` / `gtex_single_tissue_eqtls` for regulatory (eQTL) variants.

## Notes

- Read-only; hits the public GTEx Portal API, so responses depend on its uptime.
- Pinned to gtex_v10 (GENCODE v39); gene symbols are resolved to matching gencodeIds
  automatically. The `connection.command` / `args` are absolute paths for this machine —
  update them if the repo or Python environment moves.
