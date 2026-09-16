---
name: regulation_connector
description: Gene regulation — ENCODE experiments/files/biosamples, JASPAR transcription-factor binding matrices, UniBind TFBS datasets. Public APIs, no auth.
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
  - encode_search_experiments
  - encode_search_biosamples
  - encode_list_files
  - encode_get_experiment
  - encode_get_file
  - encode_get_biosample
  - jaspar_get_matrix
  - jaspar_matrix_versions
  - jaspar_list_matrices
  - jaspar_list_species
  - jaspar_list_taxa
  - jaspar_list_collections
  - jaspar_list_releases
  - unibind_search_tfbs
  - unibind_get_dataset
  - unibind_tfbs_in_region
action_schemas:
  encode_get_biosample:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: encode_get_biosampleArguments
    type: object
  encode_get_experiment:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: encode_get_experimentArguments
    type: object
  encode_get_file:
    properties:
      accession:
        title: Accession
        type: string
    required:
    - accession
    title: encode_get_fileArguments
    type: object
  encode_list_files:
    properties:
      experiment_accession:
        title: Experiment Accession
        type: string
      limit:
        default: 25
        title: Limit
        type: integer
    required:
    - experiment_accession
    title: encode_list_filesArguments
    type: object
  encode_search_biosamples:
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
    title: encode_search_biosamplesArguments
    type: object
  encode_search_experiments:
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
    title: encode_search_experimentsArguments
    type: object
  jaspar_get_matrix:
    properties:
      matrix_id:
        title: Matrix Id
        type: string
    required:
    - matrix_id
    title: jaspar_get_matrixArguments
    type: object
  jaspar_list_collections:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
    title: jaspar_list_collectionsArguments
    type: object
  jaspar_list_matrices:
    properties:
      collection:
        default: ''
        title: Collection
        type: string
      limit:
        default: 20
        title: Limit
        type: integer
      search:
        default: ''
        title: Search
        type: string
    title: jaspar_list_matricesArguments
    type: object
  jaspar_list_releases:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
    title: jaspar_list_releasesArguments
    type: object
  jaspar_list_species:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
    title: jaspar_list_speciesArguments
    type: object
  jaspar_list_taxa:
    properties:
      limit:
        default: 30
        title: Limit
        type: integer
    title: jaspar_list_taxaArguments
    type: object
  jaspar_matrix_versions:
    properties:
      base_id:
        title: Base Id
        type: string
    required:
    - base_id
    title: jaspar_matrix_versionsArguments
    type: object
  unibind_get_dataset:
    properties:
      dataset_id:
        title: Dataset Id
        type: string
    required:
    - dataset_id
    title: unibind_get_datasetArguments
    type: object
  unibind_search_tfbs:
    properties:
      limit:
        default: 20
        title: Limit
        type: integer
      tf:
        title: Tf
        type: string
    required:
    - tf
    title: unibind_search_tfbsArguments
    type: object
  unibind_tfbs_in_region:
    properties:
      chromosome:
        title: Chromosome
        type: string
      end:
        title: End
        type: integer
      genome:
        default: hg38
        title: Genome
        type: string
      start:
        title: Start
        type: integer
    required:
    - chromosome
    - start
    - end
    title: unibind_tfbs_in_regionArguments
    type: object
action_descriptions:
  encode_get_biosample: |-
    Get an ENCODE biosample's metadata.

    Args:
        accession: biosample accession (e.g. "ENCBS000AAA").
  encode_get_experiment: |-
    Get an ENCODE experiment's metadata.

    Args:
        accession: experiment accession (e.g. "ENCSR000AKB").
  encode_get_file: |-
    Get an ENCODE file's metadata (format, type, assembly, download URL).

    Args:
        accession: file accession (e.g. "ENCFF000ABC").
  encode_list_files: |-
    List the files produced by an ENCODE experiment.

    Args:
        experiment_accession: e.g. "ENCSR000AKB".
        limit: max files (default 25).
  encode_search_biosamples: |-
    Search ENCODE biosamples by keyword.

    Args:
        query: search text (e.g. "K562", "liver").
        limit: max biosamples (default 15).
  encode_search_experiments: |-
    Search ENCODE experiments by keyword (assay, target, biosample).

    Args:
        query: search text (e.g. "CTCF K562 ChIP-seq").
        limit: max experiments (default 15).
  jaspar_get_matrix: |-
    Get a JASPAR TF binding matrix (motif) by id.

    Args:
        matrix_id: JASPAR matrix id (e.g. "MA0139.1").
  jaspar_list_collections: 'List JASPAR matrix collections (e.g. CORE, CNE). Args: `limit`.'
  jaspar_list_matrices: |-
    List/search JASPAR matrices, optionally by keyword or collection.

    Args:
        search: TF name/keyword (optional).
        collection: JASPAR collection (e.g. "CORE"), optional.
        limit: max matrices (default 20).
  jaspar_list_releases: 'List JASPAR database releases. Args: `limit`.'
  jaspar_list_species: 'List species available in JASPAR. Args: `limit`.'
  jaspar_list_taxa: 'List taxonomic groups in JASPAR. Args: `limit`.'
  jaspar_matrix_versions: |-
    List all versions of a JASPAR matrix.

    Args:
        base_id: JASPAR base id without version (e.g. "MA0139").
  unibind_get_dataset: |-
    Get a UniBind TFBS dataset's metadata.

    Args:
        dataset_id: UniBind dataset id (e.g. "EXP059548.KC167_developmental_stage_6-12h_embryo.ABD-A").
  unibind_search_tfbs: |-
    Search UniBind TFBS datasets by transcription factor (or keyword).

    Args:
        tf: TF name or keyword (e.g. "CTCF").
        limit: max datasets (default 20).
  unibind_tfbs_in_region: |-
    TF binding sites overlapping a genomic region.

    NOTE: UniBind exposes no public per-region JSON API (region tracks are BED downloads
    served via the genome browser), so this returns pointers rather than a live region query.

    Args:
        chromosome, start, end: region (e.g. chr17 / 7668402 / 7687550).
        genome: assembly (default hg38).
---
# Regulation

A self-contained MCP connector for gene-regulation resources over **public** APIs
(no authentication): **ENCODE**, **JASPAR**, and **UniBind**.

## Tools

### ENCODE
- `encode_search_experiments` / `encode_search_biosamples` — search by keyword.
- `encode_list_files` — files of an experiment. Args: `experiment_accession`.
- `encode_get_experiment` / `encode_get_file` / `encode_get_biosample` — by accession.

### JASPAR (TF binding matrices)
- `jaspar_get_matrix` — matrix by id (e.g. "MA0139.1"). `jaspar_matrix_versions` — versions of a base id.
- `jaspar_list_matrices` — list/search matrices (by keyword / collection).
- `jaspar_list_species` / `jaspar_list_taxa` / `jaspar_list_collections` / `jaspar_list_releases`.

### UniBind (TFBS)
- `unibind_search_tfbs` — TFBS datasets by TF. Args: `tf`.
- `unibind_get_dataset` — dataset metadata. Args: `dataset_id`.
- `unibind_tfbs_in_region` — region-based lookup (pointers only; UniBind has no public
  per-region JSON API — region tracks are BED downloads).

## Typical workflow

1. `jaspar_list_matrices` / `jaspar_get_matrix` for a TF's binding motif.
2. `encode_search_experiments` → `encode_list_files` / `encode_get_file` for ChIP-seq data.
3. `unibind_search_tfbs` → `unibind_get_dataset` for curated TF binding-site datasets.

## Notes

- Read-only; hits public ENCODE / JASPAR / UniBind APIs, so responses depend on their uptime.
- `unibind_tfbs_in_region` returns download pointers (no public per-region JSON API exists).
  (The Claude Science original also lists UCSC Genome Browser as a region backend; UCSC is not
  reachable from this build environment, so region queries are pointer-only here.)
- The `connection` uses a relative `server.py` and `command: python`, which the connector manager resolves to absolute paths at load time, so no machine-specific paths are needed.
