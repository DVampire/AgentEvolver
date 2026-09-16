---
name: chemistry_connector
description: Small-molecule chemistry — PubChem compounds/properties/similarity, ChEBI ontology, Rhea reactions, BindingDB affinities. Aggregates four public APIs (no auth).
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
  - pubchem_search_compounds
  - pubchem_get_compounds
  - pubchem_similarity_search
  - pubchem_get_bioassay_summary
  - pubchem_get_safety
  - chebi_search
  - chebi_get_entity
  - chebi_get_ontology
  - rhea_search_reactions
  - rhea_get_reaction
  - bindingdb_ligands_by_target
  - bindingdb_targets_by_compound
action_schemas:
  bindingdb_ligands_by_target:
    properties:
      cutoff_nm:
        default: 100
        title: Cutoff Nm
        type: integer
      limit:
        default: 40
        title: Limit
        type: integer
      out_file:
        default: ''
        title: Out File
        type: string
      uniprot:
        title: Uniprot
        type: string
    required:
    - uniprot
    title: bindingdb_ligands_by_targetArguments
    type: object
  bindingdb_targets_by_compound:
    properties:
      limit:
        default: 40
        title: Limit
        type: integer
      similarity_cutoff:
        default: 0.85
        title: Similarity Cutoff
        type: number
      smiles:
        title: Smiles
        type: string
    required:
    - smiles
    title: bindingdb_targets_by_compoundArguments
    type: object
  chebi_get_entity:
    properties:
      chebi_id:
        title: Chebi Id
        type: string
    required:
    - chebi_id
    title: chebi_get_entityArguments
    type: object
  chebi_get_ontology:
    properties:
      chebi_id:
        title: Chebi Id
        type: string
    required:
    - chebi_id
    title: chebi_get_ontologyArguments
    type: object
  chebi_search:
    properties:
      limit:
        default: 10
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: chebi_searchArguments
    type: object
  pubchem_get_bioassay_summary:
    properties:
      cid:
        title: Cid
        type: integer
    required:
    - cid
    title: pubchem_get_bioassay_summaryArguments
    type: object
  pubchem_get_compounds:
    properties:
      cids:
        items:
          type: integer
        title: Cids
        type: array
      properties:
        default: ''
        title: Properties
        type: string
    required:
    - cids
    title: pubchem_get_compoundsArguments
    type: object
  pubchem_get_safety:
    properties:
      cid:
        title: Cid
        type: integer
    required:
    - cid
    title: pubchem_get_safetyArguments
    type: object
  pubchem_search_compounds:
    properties:
      limit:
        default: 10
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: pubchem_search_compoundsArguments
    type: object
  pubchem_similarity_search:
    properties:
      limit:
        default: 10
        title: Limit
        type: integer
      smiles:
        title: Smiles
        type: string
      threshold:
        default: 90
        title: Threshold
        type: integer
    required:
    - smiles
    title: pubchem_similarity_searchArguments
    type: object
  rhea_get_reaction:
    properties:
      rhea_id:
        title: Rhea Id
        type: string
    required:
    - rhea_id
    title: rhea_get_reactionArguments
    type: object
  rhea_search_reactions:
    properties:
      limit:
        default: 10
        title: Limit
        type: integer
      query:
        title: Query
        type: string
    required:
    - query
    title: rhea_search_reactionsArguments
    type: object
action_descriptions:
  bindingdb_ligands_by_target: |-
    Get measured ligand binding affinities for a protein target from BindingDB.

    Args:
        uniprot: UniProt accession of the target (e.g. "P00533" for EGFR).
        cutoff_nm: only return ligands with affinity <= this many nM (default 100 = potent).
        limit: max ligands (default 40).
    Returns ligands sorted by affinity (most potent first).
  bindingdb_targets_by_compound: |-
    Find protein targets bound by compounds structurally similar to a query molecule.

    Uses BindingDB's getTargetByCompound: does a 2D similarity search for the query
    SMILES and returns the targets its similar compounds bind, with measured affinities.

    Args:
        smiles: query molecule SMILES (e.g. "CC(=O)OC1=CC=CC=C1C(=O)O" for aspirin).
        similarity_cutoff: Tanimoto similarity threshold, 0-1 (default 0.85).
        limit: max target-affinity rows (default 40).
  chebi_get_entity: |-
    Get a ChEBI entity's definition and chemical properties.

    Args:
        chebi_id: e.g. "CHEBI:27732" (or "27732").
  chebi_get_ontology: |-
    Get a ChEBI entity's is_a hierarchy (parents and children).

    Args:
        chebi_id: e.g. "CHEBI:27732".
  chebi_search: |-
    Search ChEBI (via EBI OLS) for chemical entities by name.

    Args:
        query: chemical name (e.g. "caffeine").
        limit: max results (default 10).
  pubchem_get_bioassay_summary: |-
    Summarize a compound's PubChem bioassay activity (active/inactive counts + samples).

    Args:
        cid: PubChem compound CID.
  pubchem_get_compounds: |-
    Fetch properties for one or more PubChem CIDs.

    Args:
        cids: list of PubChem CIDs, e.g. [2519, 2244].
        properties: comma-separated PUG property names; default a useful set
            (MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey,IUPACName,XLogP).
  pubchem_get_safety: |-
    Get GHS safety classification (signal, hazard statements, pictograms) for a compound.

    Args:
        cid: PubChem compound CID.
  pubchem_search_compounds: |-
    Search PubChem compounds by name/synonym; returns CID, title, formula.

    Args:
        query: compound name (e.g. "caffeine", "aspirin").
        limit: max compounds (default 10).
  pubchem_similarity_search: |-
    2D-similarity search: find PubChem compounds similar to a SMILES.

    Args:
        smiles: query structure as SMILES.
        threshold: Tanimoto similarity threshold 0-100 (default 90).
        limit: max hits (default 10).
  rhea_get_reaction: |-
    Get a Rhea reaction's equation, participating ChEBI ids, and EC number.

    Args:
        rhea_id: e.g. "RHEA:10280" or "10280".
  rhea_search_reactions: |-
    Search Rhea biochemical reactions by keyword/compound.

    Args:
        query: e.g. "caffeine", "ATP", a ChEBI name.
        limit: max reactions (default 10).
---
# Chemistry

A self-contained MCP connector for small-molecule chemistry, aggregating four
**public** APIs (no authentication): **PubChem** (PUG REST), **ChEBI** (via EBI OLS),
**Rhea** (rhea-db.org), and **BindingDB**.

## Tools

### PubChem — compounds, properties, similarity, bioassay, safety
- `pubchem_search_compounds` — search by name; returns CID, title, formula. Args: `query`, `limit`.
- `pubchem_get_compounds` — properties for CIDs. Args: `cids` (list[int]), `properties` (comma-separated PUG props, optional).
- `pubchem_similarity_search` — 2D-similarity by SMILES. Args: `smiles`, `threshold` (0-100), `limit`.
- `pubchem_get_bioassay_summary` — active/inactive counts + sample active assays. Args: `cid`.
- `pubchem_get_safety` — GHS classification (signal, hazard statements, pictograms). Args: `cid`.

### ChEBI — ontology (via EBI OLS)
- `chebi_search` — search entities by name. Args: `query`, `limit`.
- `chebi_get_entity` — definition + formula/mass/charge/SMILES/InChIKey. Args: `chebi_id` (e.g. `CHEBI:27732`).
- `chebi_get_ontology` — is_a parents and children. Args: `chebi_id`.

### Rhea — biochemical reactions
- `rhea_search_reactions` — search reactions by keyword/compound. Args: `query`, `limit`.
- `rhea_get_reaction` — equation, status, ChEBI participants. Args: `rhea_id` (e.g. `RHEA:10280`).

### BindingDB — binding affinities
- `bindingdb_ligands_by_target` — measured ligand affinities for a protein target, sorted by potency.
  Args: `uniprot` (e.g. `P00533`), `cutoff_nm` (default 100), `limit`.
- `bindingdb_targets_by_compound` — targets bound by compounds similar to a query molecule.
  Args: `smiles`, `similarity_cutoff` (Tanimoto, default 0.85), `limit`.

## Typical workflow

1. `pubchem_search_compounds` / `chebi_search` to identify a compound (CID / ChEBI id).
2. `pubchem_get_compounds` for physicochemical properties; `chebi_get_entity` for the
   ontology definition; `pubchem_get_safety` for GHS hazards.
3. `pubchem_similarity_search` for structural analogs.
4. `rhea_search_reactions` / `rhea_get_reaction` for the metabolic reactions a compound
   participates in; `bindingdb_ligands_by_target` for a target's potent binders, or
   `bindingdb_targets_by_compound` to find what a molecule's analogs bind.

## Notes

- Read-only; hits public PubChem/ChEBI(OLS)/Rhea/BindingDB endpoints, so responses depend
  on their uptime. BindingDB responses for well-studied targets can be large — use a tight
  `cutoff_nm`.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
