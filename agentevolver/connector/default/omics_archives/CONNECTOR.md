---
name: omics_archives_connector
description: Omics data archives — ArrayExpress/BioStudies experiments, GEO series, MGnify metagenomics, PRIDE proteomics, MetaboLights metabolomics. Public EBI/NCBI APIs, no auth.
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
  - arrayexpress_search_experiments
  - arrayexpress_get_experiment
  - arrayexpress_get_experiment_files
  - arrayexpress_get_experiment_samples
  - geo_search_series
  - geo_get_series
  - mgnify_search_studies
  - mgnify_get_studies
  - mgnify_get_study_analyses
  - pride_search_projects
  - pride_get_projects
  - pride_search_project_proteins
  - pride_find_projects_for_protein
  - metabolights_list_studies
  - metabolights_get_studies
  - metabolights_get_study_files
  - metabolights_search_data_files
action_schemas:
  arrayexpress_get_experiment:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: arrayexpress_get_experimentArguments
    type: object
  arrayexpress_get_experiment_files:
    properties:
      accession:
        title: Accession
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - accession
    title: arrayexpress_get_experiment_filesArguments
    type: object
  arrayexpress_get_experiment_samples:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: arrayexpress_get_experiment_samplesArguments
    type: object
  arrayexpress_search_experiments:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: arrayexpress_search_experimentsArguments
    type: object
  geo_get_series:
    properties:
      gse:
        title: Gse
        type: string
    required:
    - gse
    title: geo_get_seriesArguments
    type: object
  geo_search_series:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: geo_search_seriesArguments
    type: object
  metabolights_get_studies:
    properties:
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    title: metabolights_get_studiesArguments
    type: object
  metabolights_get_study_files:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    title: metabolights_get_study_filesArguments
    type: object
  metabolights_list_studies:
    properties:
      limit:
        default: 40
        title: Limit
        type: integer
    title: metabolights_list_studiesArguments
    type: object
  metabolights_search_data_files:
    properties:
      query:
        default: ''
        title: Query
        type: string
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    title: metabolights_search_data_filesArguments
    type: object
  mgnify_get_studies:
    properties:
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    title: mgnify_get_studiesArguments
    type: object
  mgnify_get_study_analyses:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      study_id:
        title: Study Id
        type: string
    required:
    - study_id
    title: mgnify_get_study_analysesArguments
    type: object
  mgnify_search_studies:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: mgnify_search_studiesArguments
    type: object
  pride_find_projects_for_protein:
    properties:
      limit:
        default: 15
        title: Limit
        type: integer
      protein:
        title: Protein
        type: string
    required:
    - protein
    title: pride_find_projects_for_proteinArguments
    type: object
  pride_get_projects:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: pride_get_projectsArguments
    type: object
  pride_search_project_proteins:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: pride_search_project_proteinsArguments
    type: object
  pride_search_projects:
    properties:
      keyword:
        title: Keyword
        type: string
      limit:
        default: 15
        title: Limit
        type: integer
    required:
    - keyword
    title: pride_search_projectsArguments
    type: object
action_descriptions:
  arrayexpress_get_experiment: |-
    Get an ArrayExpress experiment's metadata.

    Args:
        accession: experiment accession (e.g. "E-GEOD-17155").
  arrayexpress_get_experiment_files: |-
    List the data files of an ArrayExpress experiment.

    Args:
        accession: experiment accession (e.g. "E-GEOD-17155").
        limit: max files (default 25).
  arrayexpress_get_experiment_samples: |-
    Summarize the samples/assays of an ArrayExpress experiment (from its SDRF).

    Args:
        accession: experiment accession (e.g. "E-GEOD-17155").
  arrayexpress_search_experiments: |-
    Search ArrayExpress (BioStudies) functional-genomics experiments.

    Args:
        query: search text (e.g. "breast cancer RNA-seq").
        limit: max experiments (default 15).
  geo_get_series: |-
    Get details of a GEO series by accession (GSE id).

    Args:
        gse: series accession (e.g. "GSE17155").
  geo_search_series: |-
    Search NCBI GEO for expression series (GSE) by keyword.

    Args:
        query: search text (e.g. "breast cancer").
        limit: max series (default 15).
  metabolights_get_studies: |-
    Get a MetaboLights study's metadata.

    Args:
        study_id: study accession (e.g. "MTBLS1").
  metabolights_get_study_files: |-
    List the files of a MetaboLights study.

    Args:
        study_id: study accession (e.g. "MTBLS1").
        limit: max files (default 30).
  metabolights_list_studies: |-
    List public MetaboLights study accessions.

    Args:
        limit: max study ids (default 40).
  metabolights_search_data_files: |-
    Search the data files of a MetaboLights study by filename substring.

    Args:
        study_id: study accession (e.g. "MTBLS1").
        query: filename substring to match (e.g. ".mzML", "sample"); empty for all data files.
  mgnify_get_studies: |-
    Get an MGnify study's metadata.

    Args:
        study_id: MGnify study accession (e.g. "MGYS00006862").
  mgnify_get_study_analyses: |-
    List the analyses of an MGnify study.

    Args:
        study_id: MGnify study accession (e.g. "MGYS00006862").
        limit: max analyses (default 20).
  mgnify_search_studies: |-
    Search MGnify metagenomics studies.

    Args:
        query: search text (e.g. "human gut microbiome").
        limit: max studies (default 15).
  pride_find_projects_for_protein: |-
    Find PRIDE projects associated with a protein (by accession/name keyword).

    Args:
        protein: protein accession or name (e.g. "P04637", "TP53").
        limit: max projects (default 15).
  pride_get_projects: |-
    Get a PRIDE project's metadata.

    Args:
        accession: PRIDE project accession (e.g. "PXD079445").
  pride_search_project_proteins: |-
    Proteins reported in a PRIDE project.

    NOTE: PRIDE exposes no simple public per-project protein-list API; this returns
    pointers to the project's protein data instead.

    Args:
        accession: PRIDE project accession (e.g. "PXD079445").
  pride_search_projects: |-
    Search PRIDE proteomics projects by keyword.

    Args:
        keyword: search text (e.g. "melanoma phosphoproteome").
        limit: max projects (default 15).
---
# Omics Archives

A self-contained MCP connector for omics data repositories, over **public** EBI/NCBI
APIs (no authentication): **ArrayExpress/BioStudies**, **GEO**, **MGnify**, **PRIDE**,
and **MetaboLights**. GEO uses NCBI E-utilities (referenced from the open-source
[MCPmed/GEOmcp](https://github.com/MCPmed/GEOmcp)).

## Tools

### ArrayExpress (BioStudies) — functional genomics
- `arrayexpress_search_experiments` — search experiments. Args: `query`, `limit`.
- `arrayexpress_get_experiment` — experiment metadata. Args: `accession`.
- `arrayexpress_get_experiment_files` — data files. Args: `accession`, `limit`.
- `arrayexpress_get_experiment_samples` — sample/SDRF summary. Args: `accession`.

### GEO — expression series
- `geo_search_series` — search GSE series. Args: `query`, `limit`.
- `geo_get_series` — series details. Args: `gse`.

### MGnify — metagenomics
- `mgnify_search_studies` / `mgnify_get_studies` / `mgnify_get_study_analyses`.

### PRIDE — proteomics
- `pride_search_projects` / `pride_get_projects`.
- `pride_find_projects_for_protein` — projects matching a protein (keyword search).
- `pride_search_project_proteins` — pointer to a project's protein data (no public
  per-project protein-list endpoint exists).

### MetaboLights — metabolomics
- `metabolights_list_studies` / `metabolights_get_studies` / `metabolights_get_study_files`
  / `metabolights_search_data_files`.

## Typical workflow

1. Pick a modality: `arrayexpress_search_experiments` / `geo_search_series` (transcriptomics),
   `pride_search_projects` (proteomics), `metabolights_list_studies` (metabolomics),
   `mgnify_search_studies` (metagenomics).
2. Fetch details (`*_get_*`) and files/analyses/samples for the chosen study.

## Notes

- Read-only; hits public EBI (BioStudies/MGnify/PRIDE/MetaboLights) and NCBI (GEO E-utils)
  endpoints, so responses depend on their uptime.
- PRIDE offers no public per-project protein-list API (`pride_search_project_proteins`
  returns pointers instead).
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
