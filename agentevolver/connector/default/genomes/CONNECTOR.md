---
name: genomes_connector
description: Genome annotation via Ensembl REST (gene lookup, cross-references, VEP, homology, sequence, region overlap) plus UCSC Genome Browser REST (track listing/data, phyloP/phastCons conservation, ENCODE TFBS clusters, chromosome sizes). Public APIs, no auth.
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
  - ensembl_lookup
  - ensembl_xrefs
  - ensembl_vep_variant
  - ensembl_homology
  - ensembl_sequence
  - ensembl_overlap_region
  - ucsc_list_tracks
  - ucsc_track_data
  - ucsc_conservation
  - ucsc_tfbs_clusters
  - ucsc_chrom_sizes
action_schemas:
  ensembl_homology:
    properties:
      gene:
        title: Gene
        type: string
      species:
        default: human
        title: Species
        type: string
      target_species:
        default: ''
        title: Target Species
        type: string
    required:
    - gene
    title: ensembl_homologyArguments
    type: object
  ensembl_lookup:
    properties:
      query:
        title: Query
        type: string
      species:
        default: human
        title: Species
        type: string
    required:
    - query
    title: ensembl_lookupArguments
    type: object
  ensembl_overlap_region:
    properties:
      feature:
        default: gene
        title: Feature
        type: string
      region:
        title: Region
        type: string
      species:
        default: human
        title: Species
        type: string
    required:
    - region
    title: ensembl_overlap_regionArguments
    type: object
  ensembl_sequence:
    properties:
      ensembl_id:
        title: Ensembl Id
        type: string
      max_len:
        default: 1000
        title: Max Len
        type: integer
      seq_type:
        default: genomic
        title: Seq Type
        type: string
    required:
    - ensembl_id
    title: ensembl_sequenceArguments
    type: object
  ensembl_vep_variant:
    properties:
      species:
        default: human
        title: Species
        type: string
      variant:
        title: Variant
        type: string
    required:
    - variant
    title: ensembl_vep_variantArguments
    type: object
  ensembl_xrefs:
    properties:
      query:
        title: Query
        type: string
      species:
        default: human
        title: Species
        type: string
    required:
    - query
    title: ensembl_xrefsArguments
    type: object
  ucsc_chrom_sizes:
    properties:
      genome:
        default: hg38
        title: Genome
        type: string
      search:
        default: ''
        title: Search
        type: string
    title: ucsc_chrom_sizesArguments
    type: object
  ucsc_conservation:
    properties:
      genome:
        default: hg38
        title: Genome
        type: string
      region:
        title: Region
        type: string
      track:
        default: ''
        title: Track
        type: string
    required:
    - region
    title: ucsc_conservationArguments
    type: object
  ucsc_list_tracks:
    properties:
      genome:
        default: hg38
        title: Genome
        type: string
      search:
        default: ''
        title: Search
        type: string
    title: ucsc_list_tracksArguments
    type: object
  ucsc_tfbs_clusters:
    properties:
      genome:
        default: hg38
        title: Genome
        type: string
      region:
        title: Region
        type: string
    required:
    - region
    title: ucsc_tfbs_clustersArguments
    type: object
  ucsc_track_data:
    properties:
      genome:
        default: hg38
        title: Genome
        type: string
      region:
        title: Region
        type: string
      track:
        title: Track
        type: string
    required:
    - track
    - region
    title: ucsc_track_dataArguments
    type: object
action_descriptions:
  ensembl_homology: "Orthologues of a gene across species via Ensembl Compara.\n\n \
    \   Args:\n        gene: gene symbol (e.g. \"BRCA1\").\n        species: source\
    \ species (default \"human\").\n        target_species: optional single target species\
    \ to restrict to (e.g. \"mouse\")."
  ensembl_lookup: "Look up a gene/transcript by Ensembl id or gene symbol.\n\n    Args:\n\
    \        query: Ensembl id (e.g. \"ENSG00000012048\") or gene symbol (e.g. \"BRCA1\"\
    ).\n        species: species for symbol lookup (default \"human\")."
  ensembl_overlap_region: "List features overlapping a genomic region via Ensembl.\n\
    \n    Args:\n        region: \"chrom:start-end\" (e.g. \"17:43044295-43125483\"\
    ).\n        species: species (default \"human\").\n        feature: feature type\
    \ — gene, transcript, exon, variation, regulatory (default \"gene\")."
  ensembl_sequence: "Fetch the sequence for an Ensembl id.\n\n    Args:\n        ensembl_id:\
    \ gene/transcript/protein id (e.g. \"ENST00000357654\").\n        seq_type: \"genomic\"\
    , \"cds\", \"cdna\", or \"protein\" (default \"genomic\").\n        max_len: max\
    \ sequence characters to return (default 1000; full length reported)."
  ensembl_vep_variant: "Predict variant effects (VEP) for an rsID, HGVS notation, or\
    \ region/allele.\n\n    Args:\n        variant: dbSNP rsID (e.g. \"rs699\"), HGVS\
    \ (e.g. \"ENST00000269305.4:c.215C>G\"),\n            or region:allele (e.g. \"\
    17:43044295:A\").\n        species: species (default \"human\")."
  ensembl_xrefs: "Cross-references (external DB ids) for a gene via Ensembl.\n\n   \
    \ Args:\n        query: Ensembl id or gene symbol (e.g. \"BRCA1\").\n        species:\
    \ species for symbol lookup (default \"human\").\n    Returns 'db<TAB>primary_id<TAB>display_id'\
    \ rows."
  ucsc_chrom_sizes: "Get chromosome/contig names and sizes for a UCSC assembly.\n\n\
    \    Args:\n        genome: UCSC assembly (e.g. \"hg38\", \"hg19\", \"mm39\").\n\
    \        search: optional substring to filter chromosome names (e.g. \"chr1\").\n\
    \    Returns 'chrom<TAB>size(bp)' rows, largest first."
  ucsc_conservation: "Summarize evolutionary conservation across a region from UCSC\
    \ phyloP/phastCons.\n\n    Args:\n        region: \"chrom:start-end\" (1-based),\
    \ e.g. \"chr17:43044295-43044395\".\n        genome: UCSC assembly (default \"hg38\"\
    ).\n        track: conservation bigWig track; defaults to \"phyloP100way\" for hg38,\n\
    \            \"phyloP60way\" otherwise. Try \"phastCons100way\" for phastCons scores.\n\
    \    Returns per-track count/mean/min/max of the base-level scores in the region."
  ucsc_list_tracks: "List queryable data tracks for a UCSC assembly.\n\n    Args:\n\
    \        genome: UCSC assembly (e.g. \"hg38\", \"hg19\", \"mm39\").\n        search:\
    \ optional case-insensitive substring to filter by track name/label.\n    Returns\
    \ 'track<TAB>type<TAB>shortLabel' rows (composite subtracks flattened)."
  ucsc_tfbs_clusters: "List ENCODE transcription-factor binding-site (TFBS) clusters\
    \ in a region.\n\n    Uses UCSC's ENCODE TF ChIP-seq clustered track (encRegTfbsClustered\
    \ on hg38 /\n    hg19). Summarizes the factors bound and their peak scores.\n\n\
    \    Args:\n        region: \"chrom:start-end\" (1-based), e.g. \"chr17:43044295-43125483\"\
    .\n        genome: UCSC assembly (default \"hg38\"; supported on hg38/hg19)."
  ucsc_track_data: "Fetch raw row data for any UCSC track within a genomic region.\n\
    \n    Args:\n        track: UCSC track name (see ucsc_list_tracks), e.g. \"refGene\"\
    , \"knownGene\".\n        region: \"chrom:start-end\" (1-based), e.g. \"chr17:43044295-43125483\"\
    .\n        genome: UCSC assembly (default \"hg38\").\n    Returns one line per feature\
    \ with its raw JSON fields."
---
# Genomes

A self-contained MCP connector for genome annotation over two **public** REST APIs,
no authentication: the **Ensembl REST API** (`rest.ensembl.org`) for gene-centric
annotation, and the **UCSC Genome Browser REST API** (`api.genome.ucsc.edu`) for
browser tracks, conservation and regulatory data.

## Tools

### Ensembl
- `ensembl_lookup` — gene/transcript by Ensembl id or symbol. Args: `query`, `species`.
- `ensembl_xrefs` — external DB cross-references for a gene. Args: `query`, `species`.
- `ensembl_vep_variant` — variant effect prediction for an rsID / HGVS. Args: `variant`, `species`.
- `ensembl_homology` — orthologues across species. Args: `gene`, `species`, `target_species`.
- `ensembl_sequence` — sequence for an id (genomic/cds/cdna/protein). Args: `ensembl_id`, `seq_type`, `max_len`.
- `ensembl_overlap_region` — features overlapping a region. Args: `region` ("chr:start-end"), `species`, `feature`.

### UCSC Genome Browser
- `ucsc_list_tracks` — queryable data tracks for an assembly (composite subtracks flattened). Args: `genome`, `search`.
- `ucsc_track_data` — raw feature rows for any track in a region. Args: `track`, `region`, `genome`.
- `ucsc_conservation` — count/mean/min/max of phyloP/phastCons scores over a region. Args: `region`, `genome`, `track`.
- `ucsc_tfbs_clusters` — ENCODE clustered TF binding sites in a region. Args: `region`, `genome`.
- `ucsc_chrom_sizes` — chromosome/contig names and sizes for an assembly. Args: `genome`, `search`.

## Typical workflow

1. `ensembl_lookup` for a gene's coordinates/id; `ensembl_xrefs` for external ids.
2. `ensembl_overlap_region` for what else is in a locus; `ensembl_sequence` for sequence.
3. `ensembl_vep_variant` for a variant's effect; `ensembl_homology` for orthologues.
4. `ucsc_list_tracks` to discover a track, then `ucsc_track_data` for its rows in a region.
5. `ucsc_conservation` / `ucsc_tfbs_clusters` for constraint and regulatory context; `ucsc_chrom_sizes` for assembly bounds.

## Notes

- Read-only; hits the public Ensembl and UCSC REST APIs, so responses depend on their uptime.
- Ensembl regions are 1-based; UCSC region strings are also given 1-based here and
  converted internally to the 0-based half-open coordinates the UCSC API expects.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
