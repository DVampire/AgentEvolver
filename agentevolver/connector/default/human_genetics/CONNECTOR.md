---
name: human_genetics_connector
description: Human genetics associations — GWAS Catalog variant/gene/trait associations & studies, eQTL Catalogue expression QTLs, and FinnGen/BioBank-Japan PheWAS. Public APIs, no auth.
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
  - gwas_associations_for_variant
  - gwas_associations_for_gene
  - gwas_associations_for_trait
  - gwas_search_traits
  - gwas_search_studies
  - gwas_get_study
  - gwas_get_variant
  - eqtl_list_datasets
  - eqtl_associations
  - phewas_instances
  - phewas_variant
  - phewas_finngen_gene
  - phewas_list_phenotypes
  - phewas_search_phenotypes
action_schemas:
  eqtl_associations:
    properties:
      dataset_id:
        title: Dataset Id
        type: string
      gene:
        default: ''
        title: Gene
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
      region:
        default: ''
        title: Region
        type: string
    required:
    - dataset_id
    title: eqtl_associationsArguments
    type: object
  eqtl_list_datasets:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
      quant_method:
        default: ''
        title: Quant Method
        type: string
      tissue:
        default: ''
        title: Tissue
        type: string
    title: eqtl_list_datasetsArguments
    type: object
  gwas_associations_for_gene:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 30
        title: Limit
        type: integer
    required:
    - gene
    title: gwas_associations_for_geneArguments
    type: object
  gwas_associations_for_trait:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
      trait:
        title: Trait
        type: string
    required:
    - trait
    title: gwas_associations_for_traitArguments
    type: object
  gwas_associations_for_variant:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
      rsid:
        title: Rsid
        type: string
    required:
    - rsid
    title: gwas_associations_for_variantArguments
    type: object
  gwas_get_study:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: gwas_get_studyArguments
    type: object
  gwas_get_variant:
    properties:
      rsid:
        title: Rsid
        type: string
    required:
    - rsid
    title: gwas_get_variantArguments
    type: object
  gwas_search_studies:
    properties:
      disease_trait:
        title: Disease Trait
        type: string
      limit:
        default: 20
        title: Limit
        type: integer
    required:
    - disease_trait
    title: gwas_search_studiesArguments
    type: object
  gwas_search_traits:
    properties:
      query:
        title: Query
        type: string
    required:
    - query
    title: gwas_search_traitsArguments
    type: object
  phewas_finngen_gene:
    properties:
      gene:
        title: Gene
        type: string
      limit:
        default: 30
        title: Limit
        type: integer
    required:
    - gene
    title: phewas_finngen_geneArguments
    type: object
  phewas_instances:
    properties: {}
    title: phewas_instancesArguments
    type: object
  phewas_list_phenotypes:
    properties:
      instance:
        default: finngen
        title: Instance
        type: string
      limit:
        default: 40
        title: Limit
        type: integer
    title: phewas_list_phenotypesArguments
    type: object
  phewas_search_phenotypes:
    properties:
      instance:
        default: finngen
        title: Instance
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: phewas_search_phenotypesArguments
    type: object
  phewas_variant:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
      rsid:
        title: Rsid
        type: string
    required:
    - rsid
    title: phewas_variantArguments
    type: object
action_descriptions:
  eqtl_associations: |-
    eQTL associations in a dataset for a gene or genomic region (top by p-value).

    Args:
        dataset_id: eQTL Catalogue dataset id (e.g. "QTD000001", see eqtl_list_datasets).
        gene: gene symbol — resolved to a region via Ensembl (optional).
        region: "chrom:start-end" (e.g. "17:7668402-7687550"); overrides gene.
        limit: max associations (default 25).
  eqtl_list_datasets: |-
    List eQTL Catalogue datasets, optionally filtered by quant method or tissue.

    Args:
        quant_method: e.g. "ge" (gene expression), "tx", "exon", "aptamer" (optional).
        tissue: substring to filter tissue label (optional).
        limit: max datasets (default 30).
  gwas_associations_for_gene: |-
    GWAS-cataloged variants mapped to a gene (their rsIDs, class, location).

    Args:
        gene: gene symbol (e.g. "APOE").
        limit: max variants (default 30).
  gwas_associations_for_trait: |-
    GWAS Catalog associations for a trait (by name or EFO id).

    Args:
        trait: trait name (e.g. "Alzheimer disease") or EFO id (e.g. "EFO_0000249").
        limit: max associations (default 30).
  gwas_associations_for_variant: |-
    GWAS Catalog associations for a variant (rsID) — its trait associations.

    Args:
        rsid: dbSNP rsID (e.g. "rs429358").
        limit: max associations (default 30).
    Returns 'pvalue<TAB>riskAllele<TAB>OR/beta<TAB>study' rows.
  gwas_get_study: |-
    Get a GWAS Catalog study by accession id.

    Args:
        accession: study accession (e.g. "GCST002245").
  gwas_get_variant: |-
    Get a GWAS Catalog variant (SNP) record by rsID.

    Args:
        rsid: dbSNP rsID (e.g. "rs429358").
  gwas_search_studies: |-
    Search GWAS Catalog studies by disease/trait. Falls back to EFO-trait studies.

    Args:
        disease_trait: reported disease/trait (e.g. "Alzheimer disease").
        limit: max studies (default 20).
  gwas_search_traits: |-
    Search GWAS Catalog EFO traits by name.

    Args:
        query: trait name (e.g. "Alzheimer disease").
    Returns 'trait<TAB>efo_id<TAB>uri' rows.
  phewas_finngen_gene: |-
    Gene-based PheWAS in FinnGen: phenotypes associated at the gene's locus.

    Args:
        gene: gene symbol (e.g. "APOE").
        limit: max phenotype associations (default 30).
  phewas_instances: List the PheWAS portals this connector can query (FinnGen, BioBank Japan).
  phewas_list_phenotypes: |-
    List phenotypes available in a PheWAS instance (FinnGen).

    Args:
        instance: PheWAS instance id (default "finngen"; see phewas_instances).
        limit: max phenotypes (default 40).
  phewas_search_phenotypes: |-
    Search phenotypes in a PheWAS instance by name/code/category (FinnGen).

    Args:
        query: search text (e.g. "diabetes", "Alzheimer").
        instance: PheWAS instance id (default "finngen").
        limit: max results (default 25).
  phewas_variant: |-
    Phenome-wide associations for a variant (all trait associations, via GWAS Catalog).

    Args:
        rsid: dbSNP rsID (e.g. "rs429358").
        limit: max associations (default 30).
---
# Human Genetics

A self-contained MCP connector for human genetics associations, aggregating **public**
resources (no authentication): **GWAS Catalog** (EMBL-EBI), **eQTL Catalogue** (EMBL-EBI),
and **FinnGen** PheWAS (public r12 release). GWAS endpoint mappings are referenced from
the open-source [koido/gwas-catalog-mcp](https://github.com/koido/gwas-catalog-mcp) (Apache-2.0).

## Tools

### GWAS Catalog
- `gwas_associations_for_variant` — trait associations for an rsID. Args: `rsid`, `limit`.
- `gwas_associations_for_gene` — GWAS-cataloged variants mapped to a gene. Args: `gene`, `limit`.
- `gwas_associations_for_trait` — associations for a trait (name/EFO id). Args: `trait`, `limit`.
- `gwas_search_traits` — search EFO traits by name. Args: `query`.
- `gwas_search_studies` — studies by disease/trait. Args: `disease_trait`, `limit`.
- `gwas_get_study` — study by accession. Args: `accession`.
- `gwas_get_variant` — SNP record by rsID. Args: `rsid`.

### eQTL Catalogue
- `eqtl_list_datasets` — QTL datasets (filter by quant method / tissue). Args: `quant_method`, `tissue`, `limit`.
- `eqtl_associations` — associations in a dataset for a gene/region. Args: `dataset_id`, `gene`, `region`, `limit`.

### PheWAS
- `phewas_instances` — list PheWAS portals (FinnGen, BioBank Japan). No args.
- `phewas_variant` — phenome-wide associations for a variant (via GWAS Catalog). Args: `rsid`, `limit`.
- `phewas_finngen_gene` — gene-based PheWAS in FinnGen. Args: `gene`, `limit`.
- `phewas_list_phenotypes` — phenotypes in a PheWAS instance. Args: `instance`, `limit`.
- `phewas_search_phenotypes` — search phenotypes by name/category. Args: `query`, `instance`, `limit`.

## Typical workflow

1. `gwas_search_traits` / `gwas_search_studies` to scope a trait; `gwas_associations_for_gene`
   / `gwas_get_variant` for a gene/variant.
2. `eqtl_list_datasets` → `eqtl_associations` for the regulatory (expression) effect of variants.
3. `phewas_finngen_gene` / `phewas_variant` for phenome-wide effects; `phewas_search_phenotypes`
   to find a phenotype code.

## Notes

- Read-only; hits public GWAS Catalog / eQTL Catalogue / FinnGen (r12) endpoints, so responses
  depend on their uptime.
- `gwas_associations_for_variant`, `gwas_associations_for_trait`, and `phewas_variant` use the
  GWAS Catalog *associations* endpoint, which can be **slow for highly-associated variants/traits**
  (e.g. APOE rs429358) — they time out gracefully with a hint if so. The other 11 tools are fast.
- PheWAS phenotype/gene tools use FinnGen's public r12 API. BioBank Japan (pheweb.jp) is listed as
  an instance but exposes only a limited public API.
- The `connection` uses a relative `server.py` and `command: python`, which the connector manager resolves to absolute paths at load time, so no machine-specific paths are needed.
