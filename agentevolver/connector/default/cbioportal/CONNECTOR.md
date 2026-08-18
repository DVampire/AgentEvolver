---
name: cbioportal_connector
description: Cancer genomics cohorts — cBioPortal studies, mutations, copy-number alterations, and clinical attributes, over the public cBioPortal REST API (no auth).
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
  - cbioportal_list_studies
  - cbioportal_get_study
  - cbioportal_mutations_in_gene
  - cbioportal_mutation_frequency
  - cbioportal_cna_in_gene
  - cbioportal_clinical_attributes
action_schemas:
  cbioportal_clinical_attributes:
    properties:
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    title: cbioportal_clinical_attributesArguments
    type: object
  cbioportal_cna_in_gene:
    properties:
      event_type:
        default: HOMDEL_AND_AMP
        title: Event Type
        type: string
      gene:
        title: Gene
        type: string
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    - gene
    title: cbioportal_cna_in_geneArguments
    type: object
  cbioportal_get_study:
    properties:
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    title: cbioportal_get_studyArguments
    type: object
  cbioportal_list_studies:
    properties:
      keyword:
        default: ''
        title: Keyword
        type: string
      limit:
        default: 50
        title: Limit
        type: integer
    title: cbioportal_list_studiesArguments
    type: object
  cbioportal_mutation_frequency:
    properties:
      gene:
        title: Gene
        type: string
      sample_list_id:
        default: ''
        title: Sample List Id
        type: string
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    - gene
    title: cbioportal_mutation_frequencyArguments
    type: object
  cbioportal_mutations_in_gene:
    properties:
      gene:
        title: Gene
        type: string
      sample_list_id:
        default: ''
        title: Sample List Id
        type: string
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    - gene
    title: cbioportal_mutations_in_geneArguments
    type: object
action_descriptions:
  cbioportal_clinical_attributes: |-
    List the clinical attributes recorded for a study's patients/samples.

    Args:
        study_id: e.g. "gbm_tcga_pan_can_atlas_2018".
    Returns 'attributeId<TAB>displayName<TAB>datatype' rows.
  cbioportal_cna_in_gene: |-
    List discrete copy-number alterations of a gene across a study's samples.

    Args:
        study_id: e.g. "gbm_tcga_pan_can_atlas_2018".
        gene: Hugo symbol or Entrez ID (e.g. "EGFR").
        event_type: one of ALL, AMP, HOMDEL, HOMDEL_AND_AMP, GAIN, HETLOSS, DIPLOID
            (default HOMDEL_AND_AMP — the clinically significant events).
    Returns 'sampleId<TAB>alteration' rows (-2 HOMDEL, -1 HETLOSS, 1 GAIN, 2 AMP).
  cbioportal_get_study: |-
    Get metadata for one study (name, cancer type, sample count, citation).

    Args:
        study_id: e.g. "gbm_tcga_pan_can_atlas_2018" (from cbioportal_list_studies).
  cbioportal_list_studies: |-
    List cancer studies (cohorts), optionally filtered by a keyword.

    Args:
        keyword: filter studies by name/id (e.g. "glioblastoma", "breast", "tcga").
        limit: max studies to return (default 50).
    Returns 'studyId<TAB>name<TAB>samples' rows.
  cbioportal_mutation_frequency: |-
    Compute how often a gene is mutated in a study (mutated samples / total).

    Args:
        study_id: e.g. "gbm_tcga_pan_can_atlas_2018".
        gene: Hugo symbol or Entrez ID (e.g. "TP53").
        sample_list_id: sample list (default "<study_id>_all").
  cbioportal_mutations_in_gene: |-
    List mutations of a gene across the samples in a study.

    Args:
        study_id: e.g. "gbm_tcga_pan_can_atlas_2018".
        gene: Hugo symbol or Entrez ID (e.g. "TP53").
        sample_list_id: sample list (default "<study_id>_all").
    Returns 'sampleId<TAB>proteinChange<TAB>mutationType' rows.
---
# Cancer Models (cBioPortal)

A self-contained MCP connector over the **public** cBioPortal REST API
(`https://www.cbioportal.org/api`). No authentication. Explore cancer genomics
cohorts: studies, somatic mutations, copy-number alterations, and clinical
attributes. Gene arguments accept either a Hugo symbol (e.g. `TP53`) or an Entrez
ID; molecular-profile and sample-list IDs are resolved automatically per study.

## Tools

### cbioportal_list_studies
List cancer studies (cohorts), optionally filtered by keyword.
- `keyword` (str, optional, e.g. "glioblastoma", "breast", "tcga"), `limit` (int, optional).

### cbioportal_get_study
Metadata for one study (name, cancer type, sample count, citation).
- `study_id` (str, e.g. `gbm_tcga_pan_can_atlas_2018`).

### cbioportal_mutations_in_gene
List a gene's mutations across a study's samples.
- `study_id` (str), `gene` (str, symbol or Entrez), `sample_list_id` (str, optional; default `<study>_all`).

### cbioportal_mutation_frequency
Fraction of samples in a study mutated in a gene (mutated / total).
- `study_id` (str), `gene` (str), `sample_list_id` (str, optional).

### cbioportal_cna_in_gene
Discrete copy-number alterations of a gene across a study.
- `study_id` (str), `gene` (str), `event_type` (str, optional: ALL / AMP / HOMDEL /
  HOMDEL_AND_AMP / GAIN / HETLOSS / DIPLOID; default HOMDEL_AND_AMP).
  Alteration codes: -2 HOMDEL, -1 HETLOSS, 1 GAIN, 2 AMP.

### cbioportal_clinical_attributes
Clinical attributes recorded for a study's patients/samples.
- `study_id` (str).

## Typical workflow

1. `cbioportal_list_studies(keyword=...)` to find a cohort, then `cbioportal_get_study`
   for its details and sample count.
2. `cbioportal_mutation_frequency` / `cbioportal_mutations_in_gene` for somatic mutations,
   and `cbioportal_cna_in_gene` for amplifications/deletions of a gene of interest.
3. `cbioportal_clinical_attributes` to see what clinical variables are available for
   correlation.

## Notes

- Read-only; hits the public cBioPortal API, so responses depend on its uptime.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
