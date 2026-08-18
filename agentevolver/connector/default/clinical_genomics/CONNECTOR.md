---
name: clinical_genomics_connector
description: Clinical genomics knowledge — ClinGen gene validity/dosage/variant curations, CIViC clinical evidence & assertions, Open Targets target/disease/drug associations. Aggregates three public sources (no auth).
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
  - clingen_dosage_sensitivity
  - clingen_gene_validity
  - clingen_variant_classifications
  - clingen_actionability
  - civic_search_genes
  - civic_gene_variants
  - civic_get_variant
  - civic_search_variants
  - civic_get_molecular_profile
  - civic_search_molecular_profiles
  - civic_get_evidence_item
  - civic_search_evidence
  - civic_get_assertion
  - civic_search_assertions
  - civic_search_diseases
  - civic_search_therapies
  - open_targets_graphql
  - open_targets_disease_targets
  - open_targets_disease_drugs
  - open_targets_drug
action_schemas:
  civic_gene_variants:
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
    title: civic_gene_variantsArguments
    type: object
  civic_get_assertion:
    properties:
      assertion_id:
        title: Assertion Id
        type: integer
    required:
    - assertion_id
    title: civic_get_assertionArguments
    type: object
  civic_get_evidence_item:
    properties:
      evidence_id:
        title: Evidence Id
        type: integer
    required:
    - evidence_id
    title: civic_get_evidence_itemArguments
    type: object
  civic_get_molecular_profile:
    properties:
      molecular_profile_id:
        title: Molecular Profile Id
        type: integer
    required:
    - molecular_profile_id
    title: civic_get_molecular_profileArguments
    type: object
  civic_get_variant:
    properties:
      variant_id:
        title: Variant Id
        type: integer
    required:
    - variant_id
    title: civic_get_variantArguments
    type: object
  civic_search_assertions:
    properties:
      disease:
        default: ''
        title: Disease
        type: string
      limit:
        default: 20
        title: Limit
        type: integer
    title: civic_search_assertionsArguments
    type: object
  civic_search_diseases:
    properties:
      limit:
        default: 25
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: civic_search_diseasesArguments
    type: object
  civic_search_evidence:
    properties:
      disease:
        default: ''
        title: Disease
        type: string
      limit:
        default: 20
        title: Limit
        type: integer
    title: civic_search_evidenceArguments
    type: object
  civic_search_genes:
    properties:
      symbol:
        title: Symbol
        type: string
    required:
    - symbol
    title: civic_search_genesArguments
    type: object
  civic_search_molecular_profiles:
    properties:
      limit:
        default: 25
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: civic_search_molecular_profilesArguments
    type: object
  civic_search_therapies:
    properties:
      limit:
        default: 25
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: civic_search_therapiesArguments
    type: object
  civic_search_variants:
    properties:
      limit:
        default: 25
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: civic_search_variantsArguments
    type: object
  clingen_actionability:
    properties:
      gene:
        title: Gene
        type: string
    required:
    - gene
    title: clingen_actionabilityArguments
    type: object
  clingen_dosage_sensitivity:
    properties:
      gene:
        title: Gene
        type: string
    required:
    - gene
    title: clingen_dosage_sensitivityArguments
    type: object
  clingen_gene_validity:
    properties:
      gene:
        title: Gene
        type: string
    required:
    - gene
    title: clingen_gene_validityArguments
    type: object
  clingen_variant_classifications:
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
    title: clingen_variant_classificationsArguments
    type: object
  open_targets_disease_drugs:
    properties:
      disease:
        title: Disease
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - disease
    title: open_targets_disease_drugsArguments
    type: object
  open_targets_disease_targets:
    properties:
      disease:
        title: Disease
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - disease
    title: open_targets_disease_targetsArguments
    type: object
  open_targets_drug:
    properties:
      drug:
        title: Drug
        type: string
    required:
    - drug
    title: open_targets_drugArguments
    type: object
  open_targets_graphql:
    properties:
      query:
        title: Query
        type: string
      variables:
        default: ''
        title: Variables
        type: string
    required:
    - query
    title: open_targets_graphqlArguments
    type: object
action_descriptions:
  civic_gene_variants: "List CIViC variants curated for a gene.\n\n    Args:\n     \
    \   gene: gene symbol (e.g. \"BRAF\").\n        limit: max variants (default 40)."
  civic_get_assertion: "Get a CIViC assertion (a summarized clinical statement over\
    \ evidence).\n\n    Args:\n        assertion_id: CIViC assertion id (integer)."
  civic_get_evidence_item: "Get a CIViC evidence item's clinical interpretation.\n\n\
    \    Args:\n        evidence_id: CIViC evidence item id (integer)."
  civic_get_molecular_profile: "Get a CIViC molecular profile (a variant or combination\
    \ interpreted clinically).\n\n    Args:\n        molecular_profile_id: CIViC molecular\
    \ profile id (integer)."
  civic_get_variant: "Get a CIViC variant's details.\n\n    Args:\n        variant_id:\
    \ CIViC variant id (integer)."
  civic_search_assertions: "Search CIViC assertions, optionally filtered by disease\
    \ name.\n\n    Args:\n        disease: disease name filter (e.g. \"Melanoma\");\
    \ empty for latest.\n        limit: max assertions (default 20)."
  civic_search_diseases: "Search CIViC diseases by name.\n\n    Args:\n        query:\
    \ disease name substring (e.g. \"melanoma\").\n        limit: max results (default\
    \ 25)."
  civic_search_evidence: "Search CIViC evidence items, optionally filtered by disease\
    \ name.\n\n    Args:\n        disease: disease name filter (e.g. \"Melanoma\");\
    \ empty for latest evidence.\n        limit: max items (default 20)."
  civic_search_genes: "Look up a gene in CIViC by symbol; returns CIViC id, name, Entrez\
    \ id, description.\n\n    Args:\n        symbol: gene symbol (e.g. \"BRAF\")."
  civic_search_molecular_profiles: "Search CIViC molecular profiles by name (e.g. \"\
    BRAF V600E\").\n\n    Args:\n        query: molecular profile name substring.\n\
    \        limit: max results (default 25)."
  civic_search_therapies: "Search CIViC therapies (drugs) by name.\n\n    Args:\n  \
    \      query: therapy name substring (e.g. \"vemurafenib\").\n        limit: max\
    \ results (default 25)."
  civic_search_variants: "Search CIViC variants by name (e.g. \"V600E\").\n\n    Args:\n\
    \        query: variant name substring.\n        limit: max results (default 25)."
  clingen_actionability: "ClinGen clinical actionability for a gene.\n\n    NOTE: ClinGen\
    \ Actionability exposes no public JSON API (web UI only), so this returns\n    the\
    \ direct report-search link for the gene rather than structured data.\n\n    Args:\n\
    \        gene: HGNC gene symbol (e.g. \"BRCA1\")."
  clingen_dosage_sensitivity: "ClinGen dosage sensitivity (haploinsufficiency / triplosensitivity)\
    \ for a gene.\n\n    Args:\n        gene: HGNC gene symbol (e.g. \"BRCA1\")."
  clingen_gene_validity: "ClinGen gene-disease validity classifications for a gene.\n\
    \n    Args:\n        gene: HGNC gene symbol (e.g. \"BRCA1\").\n    Returns 'disease<TAB>MOI<TAB>classification<TAB>date'\
    \ rows."
  clingen_variant_classifications: "ClinGen Evidence Repository (ERepo) variant interpretations\
    \ for a gene.\n\n    Args:\n        gene: HGNC gene symbol (e.g. \"BRCA1\").\n \
    \       limit: max variants (default 25).\n    Returns 'variant(HGVS)<TAB>caid<TAB>condition<TAB>date'\
    \ rows."
  open_targets_disease_drugs: "List drugs and clinical candidates for a disease from\
    \ Open Targets.\n\n    Args:\n        disease: disease name or EFO/MONDO id (e.g.\
    \ \"melanoma\").\n        limit: max drugs (default 25)."
  open_targets_disease_targets: "List targets associated with a disease, ranked by Open\
    \ Targets association score.\n\n    Args:\n        disease: disease name or EFO/MONDO\
    \ id (e.g. \"melanoma\" or \"MONDO_0005105\").\n        limit: max targets (default\
    \ 25)."
  open_targets_drug: "Get an Open Targets drug's type, phase, mechanism(s), and indications.\n\
    \n    Args:\n        drug: drug name or ChEMBL id (e.g. \"vemurafenib\" or \"CHEMBL1229517\"\
    )."
  open_targets_graphql: "Run an arbitrary Open Targets Platform GraphQL query (escape\
    \ hatch for any query).\n\n    Args:\n        query: a GraphQL query string against\
    \ https://api.platform.opentargets.org/api/v4/graphql.\n        variables: optional\
    \ JSON string of query variables.\n    Returns the JSON `data` payload."
---
# Clinical Genomics

A self-contained MCP connector aggregating three **public** clinical-genomics resources
(no authentication): **ClinGen**, **CIViC**, and **Open Targets**.

## Tools

### ClinGen — gene/variant curations
- `clingen_gene_validity` — gene-disease validity classifications. Args: `gene`.
- `clingen_dosage_sensitivity` — haploinsufficiency / triplosensitivity. Args: `gene`.
- `clingen_variant_classifications` — Evidence Repository variant interpretations. Args: `gene`, `limit`.
- `clingen_actionability` — actionability report link (no public JSON API). Args: `gene`.

### CIViC — clinical interpretation of variants (GraphQL)
- `civic_search_genes` — gene by symbol → CIViC id + description. Args: `symbol`.
- `civic_gene_variants` — variants curated for a gene. Args: `gene`, `limit`.
- `civic_get_variant` — variant details. Args: `variant_id`.
- `civic_search_variants` — variants by name (e.g. "V600E"). Args: `query`, `limit`.
- `civic_get_molecular_profile` / `civic_search_molecular_profiles` — molecular profiles. Args: `molecular_profile_id` / `query`.
- `civic_get_evidence_item` / `civic_search_evidence` — clinical evidence (by id / disease). Args: `evidence_id` / `disease`.
- `civic_get_assertion` / `civic_search_assertions` — summarized assertions. Args: `assertion_id` / `disease`.
- `civic_search_diseases` — diseases by name. Args: `query`.
- `civic_search_therapies` — therapies/drugs by name. Args: `query`.

### Open Targets — target/disease/drug associations (GraphQL)
- `open_targets_graphql` — arbitrary GraphQL passthrough. Args: `query`, `variables` (JSON string).
- `open_targets_disease_targets` — targets associated with a disease (scored). Args: `disease`, `limit`.
- `open_targets_disease_drugs` — drugs/clinical candidates for a disease. Args: `disease`, `limit`.
- `open_targets_drug` — drug type, phase, mechanism, indications. Args: `drug` (name or ChEMBL id).

Gene args accept HGNC symbols; Open Targets args accept names or ontology ids
(ENSG / EFO / MONDO / CHEMBL), auto-resolved via Open Targets search.

## Typical workflow

1. `civic_search_genes` / `clingen_gene_validity` for a gene's clinical validity & interpretations.
2. `civic_search_variants` → `civic_get_variant` → `civic_search_evidence` / `civic_get_assertion`
   for variant-level clinical evidence and therapy implications.
3. `clingen_dosage_sensitivity` / `clingen_variant_classifications` for dosage & variant pathogenicity.
4. `open_targets_disease_targets` / `open_targets_disease_drugs` / `open_targets_drug` for the
   target-disease-drug landscape; `open_targets_graphql` for anything else.

## Notes

- Read-only; hits public ClinGen (FTP/ERepo), CIViC (GraphQL), and Open Targets (GraphQL)
  endpoints, so responses depend on their uptime. ClinGen actionability has no public API.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
