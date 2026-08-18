---
name: molecule_toolkit_connector
description: Local cheminformatics via RDKit — convert molecules between SMILES/MOL/SDF/InChI, render 2D structure depictions as SVG, compute molecular descriptors, extract scaffolds, and parse reaction SMILES. Fully local, no network, no auth.
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
  - molecule_convert
  - molecule_descriptors
  - molecule_depict
  - molecule_scaffold
  - reaction_parse
action_schemas:
  molecule_convert:
    properties:
      from_format:
        default: smiles
        title: From Format
        type: string
      structure:
        title: Structure
        type: string
      to_format:
        default: mol
        title: To Format
        type: string
    required:
    - structure
    title: molecule_convertArguments
    type: object
  molecule_depict:
    properties:
      height:
        default: 300
        title: Height
        type: integer
      smiles:
        title: Smiles
        type: string
      width:
        default: 400
        title: Width
        type: integer
    required:
    - smiles
    title: molecule_depictArguments
    type: object
  molecule_descriptors:
    properties:
      smiles:
        title: Smiles
        type: string
    required:
    - smiles
    title: molecule_descriptorsArguments
    type: object
  molecule_scaffold:
    properties:
      generic:
        default: false
        title: Generic
        type: boolean
      smiles:
        title: Smiles
        type: string
    required:
    - smiles
    title: molecule_scaffoldArguments
    type: object
  reaction_parse:
    properties:
      reaction_smiles:
        title: Reaction Smiles
        type: string
    required:
    - reaction_smiles
    title: reaction_parseArguments
    type: object
action_descriptions:
  molecule_convert: "Convert a molecule between chemical formats.\n\n    Args:\n   \
    \     structure: the molecule in `from_format` (e.g. a SMILES string or a MOL block).\n\
    \        from_format: input format — smiles / mol / sdf / inchi / smarts.\n    \
    \    to_format: output format — smiles / mol / inchi / inchikey / formula."
  molecule_depict: "Render a 2D structure depiction of a molecule as an SVG string (embeddable\
    \ in reports).\n\n    Args:\n        smiles: molecule as SMILES.\n        width:\
    \ image width in px (default 400).\n        height: image height in px (default\
    \ 300)."
  molecule_descriptors: "Compute key molecular descriptors and Lipinski rule-of-five\
    \ for a molecule.\n\n    Args:\n        smiles: molecule as SMILES (e.g. \"CC(=O)Oc1ccccc1C(=O)O\"\
    )."
  molecule_scaffold: "Extract the Bemis-Murcko scaffold (core ring system) of a molecule.\n\
    \n    Args:\n        smiles: molecule as SMILES.\n        generic: if True, return\
    \ the generic (atom/bond-agnostic) scaffold framework."
  reaction_parse: "Parse a reaction SMILES (reactants>>products) into its components.\n\
    \n    Args:\n        reaction_smiles: e.g. \"CC(=O)O.OCC>>CC(=O)OCC.O\"."
---
# Molecule Toolkit

A self-contained MCP connector for cheminformatics using the open-source **RDKit**
library — fully **local** (no network, no auth). This is the agent-usable counterpart
of an interactive molecule sketcher: instead of a UI canvas for a human to draw,
it provides the programmatic molecule operations an autonomous agent can call, and it
handles the same `.smi/.mol/.sdf/.rxn` (and InChI) formats.

## Tools

- `molecule_convert` — convert between formats. Args: `structure`, `from_format`
  (smiles/mol/sdf/inchi/smarts), `to_format` (smiles/mol/inchi/inchikey/formula).
- `molecule_descriptors` — MW, formula, logP, TPSA, HBD/HBA, rings, InChIKey, Lipinski Ro5.
  Args: `smiles`.
- `molecule_depict` — render a 2D structure as an **SVG** string (embeddable in a report).
  Args: `smiles`, `width`, `height`.
- `molecule_scaffold` — Bemis-Murcko scaffold (optionally generic). Args: `smiles`, `generic`.
- `reaction_parse` — parse a reaction SMILES (reactants>>products) into components.
  Args: `reaction_smiles`.

## Typical workflow

1. `molecule_convert` to normalize an input (e.g. name/MOL → canonical SMILES / InChIKey).
2. `molecule_descriptors` for physicochemical properties and drug-likeness.
3. `molecule_depict` to produce an SVG structure image to embed in a report/artifact.
4. `molecule_scaffold` / `reaction_parse` for core-structure and reaction analysis.

## Notes

- Fully local (RDKit); no external API, so it is fast and always available offline.
- This connector does **not** provide an interactive drawing canvas (that requires a
  client-side UI surface); it provides the equivalent programmatic molecule operations.
- The `connection` above uses a relative `server.py` and `command: python`; the connector
  manager resolves both at load time (`server.py` → this connector's directory, `python` →
  the running interpreter via `sys.executable`), so no machine-specific paths are needed.
