---
name: zinc_connector
description: ZINC22 purchasable chemical space via CartBlanche22 — look up compounds by ZINC id, SMILES (exact/similarity) or supplier catalog number, draw random samples, and locate docking-ready 3D structures. Public API, no auth.
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
  - zinc_search_by_id
  - zinc_search_by_smiles
  - zinc_search_by_supplier
  - zinc_random_sample
  - zinc_get_3d
action_schemas:
  zinc_get_3d:
    properties:
      zinc_id:
        title: Zinc Id
        type: string
    required:
    - zinc_id
    title: zinc_get_3dArguments
    type: object
  zinc_random_sample:
    properties:
      count:
        default: 10
        title: Count
        type: integer
    title: zinc_random_sampleArguments
    type: object
  zinc_search_by_id:
    properties:
      zinc_ids:
        items:
          type: string
        title: Zinc Ids
        type: array
    required:
    - zinc_ids
    title: zinc_search_by_idArguments
    type: object
  zinc_search_by_smiles:
    properties:
      anonymous_distance:
        default: 0
        title: Anonymous Distance
        type: integer
      distance:
        default: 0
        title: Distance
        type: integer
      smiles:
        title: Smiles
        type: string
    required:
    - smiles
    title: zinc_search_by_smilesArguments
    type: object
  zinc_search_by_supplier:
    properties:
      supplier_codes:
        items:
          type: string
        title: Supplier Codes
        type: array
    required:
    - supplier_codes
    title: zinc_search_by_supplierArguments
    type: object
action_descriptions:
  zinc_get_3d: |-
    Locate the docking-ready 3D structure of a ZINC22 compound.

    ZINC22 stores pre-generated 3D conformers (db2/mol2) organized by tranche. This
    returns the substance's tranche and the corresponding files.docking.org location,
    plus its ZINC page, so the 3D structure can be downloaded for docking.

    Args:
        zinc_id: ZINC id (e.g. "ZINC000000000053").
  zinc_random_sample: |-
    Draw a random sample of purchasable ZINC22 substances.

    Args:
        count: number of random substances to return (default 10, capped at 60).
  zinc_search_by_id: |-
    Look up purchasable ZINC22 compounds by ZINC id, with their supplier catalogs.

    Args:
        zinc_ids: one or more ZINC ids (e.g. ["ZINC000000000053"]).
    Returns each substance's SMILES and the supplier catalog/codes offering it.
  zinc_search_by_smiles: |-
    Search purchasable ZINC22 compounds by SMILES — exact or similarity search.

    Args:
        smiles: query SMILES (e.g. "c1ccc(cc1)C(=O)O").
        distance: SMILES/ECFP graph distance for similarity; 0 = exact match, higher = fuzzier.
        anonymous_distance: element-anonymized graph distance (0 = off); relaxes atom identity.
    Returns matching purchasable substances with SMILES and suppliers.
  zinc_search_by_supplier: |-
    Resolve supplier catalog numbers to ZINC22 substances.

    Args:
        supplier_codes: vendor catalog numbers / supplier codes to look up.
    Returns the ZINC substances that map to those catalog entries.
---
# ZINC22 (CartBlanche22)

A self-contained MCP connector over the **public** CartBlanche22 API
(`https://cartblanche22.docking.org`), the search front-end for **ZINC22** — Irwin &
Shoichet's catalog of purchasable ("make-on-demand" + in-stock) compounds used for
virtual screening and docking. No authentication.

CartBlanche's substance / SMILES / supplier searches are **asynchronous**: a POST
returns a task id and the results are polled from `/search/result/<task>.json`. This
connector wraps that task-and-poll flow so each tool call returns finished results.

## Tools

- `zinc_search_by_id` — look up compounds by ZINC id, with supplier catalogs. Args: `zinc_ids` (list).
- `zinc_search_by_smiles` — exact or similarity structure search. Args: `smiles`, `distance` (0 = exact), `anonymous_distance`.
- `zinc_search_by_supplier` — resolve supplier catalog numbers to ZINC substances. Args: `supplier_codes` (list).
- `zinc_random_sample` — random sample of purchasable substances. Args: `count`.
- `zinc_get_3d` — locate the docking-ready 3D structure (tranche + files.docking.org path). Args: `zinc_id`.

## Typical workflow

1. `zinc_search_by_smiles` (with a `distance` for analogs) or `zinc_search_by_id` to find
   purchasable compounds; `zinc_search_by_supplier` to go from a vendor catalog number to ZINC.
2. `zinc_get_3d` to locate the pre-generated 3D conformers for docking.
3. `zinc_random_sample` for a quick, unbiased sample of the purchasable space.

## Notes

- Read-only; hits the public CartBlanche22 API, so responses depend on its uptime. The
  search endpoints are asynchronous and can be slow — the tools poll for completion and
  will report if a search is still processing.
- 3D conformers (db2/mol2) are organized by tranche under `files.docking.org`; see
  wiki.docking.org (ZINC22:Downloading) for the tranche layout.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
