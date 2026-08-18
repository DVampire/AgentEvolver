---
name: variants_connector
description: Human genetic variants — gnomAD population frequencies/constraint (r4), ClinVar records/search (direct NCBI), dbSNP, structural and mitochondrial variants. Public APIs, no auth.
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
  - get_variant
  - gene_variants
  - search_variants
  - gene_constraint
  - region_variants
  - liftover_variant
  - structural_variants
  - get_structural_variant
  - mitochondrial_variants
  - clinvar_variants
  - clinvar_search
  - clinvar_get_records
  - clinvar_variant_by_rsid
  - dbsnp_get_rsids
  - dbsnp_search_by_region
action_schemas:
  clinvar_get_records:
    properties:
      clinvar_ids:
        title: Clinvar Ids
        type: string
    required:
    - clinvar_ids
    title: clinvar_get_recordsArguments
    type: object
  clinvar_search:
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
    title: clinvar_searchArguments
    type: object
  clinvar_variant_by_rsid:
    properties:
      rsid:
        title: Rsid
        type: string
    required:
    - rsid
    title: clinvar_variant_by_rsidArguments
    type: object
  clinvar_variants:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 20
        title: Limit
        type: integer
    required:
    - gene
    title: clinvar_variantsArguments
    type: object
  dbsnp_get_rsids:
    properties:
      rsids:
        title: Rsids
        type: string
    required:
    - rsids
    title: dbsnp_get_rsidsArguments
    type: object
  dbsnp_search_by_region:
    properties:
      chrom:
        title: Chrom
        type: string
      end:
        title: End
        type: integer
      limit:
        default: 25
        title: Limit
        type: integer
      start:
        title: Start
        type: integer
    required:
    - chrom
    - start
    - end
    title: dbsnp_search_by_regionArguments
    type: object
  gene_constraint:
    properties:
      gene:
        title: Gene
        type: string
    required:
    - gene
    title: gene_constraintArguments
    type: object
  gene_variants:
    properties:
      dataset:
        default: gnomad_r4
        title: Dataset
        type: string
      gene:
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - gene
    title: gene_variantsArguments
    type: object
  get_structural_variant:
    properties:
      sv_id:
        title: Sv Id
        type: string
    required:
    - sv_id
    title: get_structural_variantArguments
    type: object
  get_variant:
    properties:
      dataset:
        default: gnomad_r4
        title: Dataset
        type: string
      variant_id:
        title: Variant Id
        type: string
    required:
    - variant_id
    title: get_variantArguments
    type: object
  liftover_variant:
    properties:
      source_genome:
        default: GRCh38
        title: Source Genome
        type: string
      variant_id:
        title: Variant Id
        type: string
    required:
    - variant_id
    title: liftover_variantArguments
    type: object
  mitochondrial_variants:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - gene
    title: mitochondrial_variantsArguments
    type: object
  region_variants:
    properties:
      chrom:
        title: Chrom
        type: string
      dataset:
        default: gnomad_r4
        title: Dataset
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
      start:
        title: Start
        type: integer
      stop:
        title: Stop
        type: integer
    required:
    - chrom
    - start
    - stop
    title: region_variantsArguments
    type: object
  search_variants:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - gene
    title: search_variantsArguments
    type: object
  structural_variants:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - gene
    title: structural_variantsArguments
    type: object
action_descriptions:
  clinvar_get_records: "Get specific ClinVar records by id.\n\n    Args:\n        clinvar_ids:\
    \ comma-separated ClinVar UIDs (e.g. \"4856951,446748\")."
  clinvar_search: "Search ClinVar by free-text query.\n\n    Args:\n        query: search\
    \ term (e.g. \"BRCA1 pathogenic\", \"Lynch syndrome\").\n        limit: max records\
    \ (default 20)."
  clinvar_variant_by_rsid: "Find ClinVar records for a dbSNP rsID.\n\n    Args:\n  \
    \      rsid: dbSNP rsID (e.g. \"rs334\" or \"334\")."
  clinvar_variants: "List ClinVar variant records for a gene.\n\n    Args:\n       \
    \ gene: gene symbol (e.g. \"BRCA1\").\n        limit: max records (default 20)."
  dbsnp_get_rsids: "Get dbSNP records for one or more rsIDs (alleles, MAF, clinical\
    \ significance, genes).\n\n    Args:\n        rsids: comma-separated rsIDs (e.g.\
    \ \"rs334,rs7412\")."
  dbsnp_search_by_region: "Find dbSNP rsIDs in a genomic region (GRCh38).\n\n    Args:\n\
    \        chrom: chromosome number (e.g. \"11\").\n        start, end: base positions.\n\
    \        limit: max rsIDs (default 25)."
  gene_constraint: "Get gnomAD loss-of-function / missense constraint metrics for a\
    \ gene.\n\n    Args:\n        gene: gene symbol (e.g. \"PCSK9\")."
  gene_variants: "List gnomAD variants in a gene (by symbol), highest allele frequency\
    \ first.\n\n    Args:\n        gene: gene symbol (e.g. \"PCSK9\").\n        limit:\
    \ max variants (default 25).\n        dataset: gnomAD dataset (default \"gnomad_r4\"\
    )."
  get_structural_variant: "Get a gnomAD structural variant by id.\n\n    Args:\n   \
    \     sv_id: structural variant id (e.g. \"DEL_1_12345\")."
  get_variant: "Get a gnomAD variant's population frequencies (genome & exome).\n\n\
    \    Args:\n        variant_id: e.g. \"1-55039974-G-T\" (chrom-pos-ref-alt, GRCh38).\n\
    \        dataset: gnomAD dataset (default \"gnomad_r4\")."
  liftover_variant: "Lift a variant over between GRCh37 and GRCh38 via gnomAD.\n\n \
    \   Args:\n        variant_id: chrom-pos-ref-alt in the source assembly.\n     \
    \   source_genome: \"GRCh38\" or \"GRCh37\" (default GRCh38)."
  mitochondrial_variants: "List gnomAD mitochondrial variants in a gene.\n\n    Args:\n\
    \        gene: mitochondrial gene symbol (e.g. \"MT-ND1\").\n        limit: max\
    \ variants (default 25)."
  region_variants: "List gnomAD variants in a genomic region.\n\n    Args:\n       \
    \ chrom: chromosome (e.g. \"1\"). start, stop: 1-based coordinates (GRCh38).\n \
    \       limit: max variants (default 25). dataset: default \"gnomad_r4\"."
  search_variants: "Search gnomAD variants within a gene (alias of gene_variants by\
    \ symbol).\n\n    Args:\n        gene: gene symbol (e.g. \"BRCA1\").\n        limit:\
    \ max variants (default 25)."
  structural_variants: "List gnomAD structural variants (SVs) overlapping a gene.\n\n\
    \    Args:\n        gene: gene symbol (e.g. \"COL1A1\").\n        limit: max SVs\
    \ (default 25)."
---
# Variants

A self-contained MCP connector for human genetic variants over **public** APIs
(no authentication): **gnomAD** (GraphQL), **ClinVar** and **dbSNP** (NCBI E-utilities).

## Tools

### gnomAD (population frequencies & constraint, r4)
- `get_variant` (by id) / `search_variants` / `gene_variants` (by gene) / `region_variants`.
- `gene_constraint` (pLI, o/e LoF & missense). `liftover_variant` (GRCh37↔38).
- `structural_variants` / `get_structural_variant`. `mitochondrial_variants`.

### ClinVar (clinical significance, NCBI)
- `clinvar_variants` (by gene) / `clinvar_search` (free text) / `clinvar_get_records` (by id) /
  `clinvar_variant_by_rsid`.

### dbSNP (NCBI)
- `dbsnp_get_rsids` (alleles, MAF, clinical sig, genes) / `dbsnp_search_by_region`.

## Typical workflow

1. `dbsnp_get_rsids` / `get_variant` for a specific variant; `clinvar_variant_by_rsid` for its
   clinical significance.
2. `gene_variants` / `gene_constraint` for a gene's variation and tolerance; `region_variants`
   / `dbsnp_search_by_region` for a locus.

## Notes

- Read-only; ClinVar/dbSNP hit NCBI E-utilities (rate-limited ~3 req/s; the connector retries on 429).
- The **gnomAD** API is 403-blocked from some networks (including this build sandbox). The gnomAD
  tools follow the official gnomAD GraphQL schema and work where the API is reachable; they degrade
  gracefully with a clear message otherwise. ClinVar/dbSNP tools are verified.
- The `connection` uses a relative `server.py` and `command: python`, which the connector manager resolves to absolute paths at load time, so no machine-specific paths are needed.
