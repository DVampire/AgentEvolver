---
name: chembl_connector
description: EMBL-EBI ChEMBL database (v34) — search compounds, targets, bioactivity, mechanisms of action, approved drugs, and ADMET properties.
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: streamable_http
  url: https://hcls.mcp.claude.com/chembl/mcp
actions:
  - compound_search
  - get_bioactivity
  - target_search
  - get_mechanism
  - drug_search
  - get_admet
action_schemas:
  compound_search:
    additionalProperties: false
    properties:
      chembl_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: ChEMBL identifier (e.g., 'CHEMBL25' for aspirin)
      limit:
        default: 20
        description: Maximum number of results to return
        maximum: 1000
        minimum: 1
        type: integer
      max_phase:
        anyOf:
        - enum:
          - 0
          - 1
          - 2
          - 3
          - 4
          type: integer
        - type: 'null'
        default: null
        description: Filter by clinical phase. Use 4 for approved drugs only
      name:
        description: Compound name or synonym to search for (e.g., 'aspirin', 'imatinib'). Case-insensitive search. This is the primary search criterion.
        type: string
      similarity_threshold:
        anyOf:
        - maximum: 100
          minimum: 70
          type: integer
        - type: 'null'
        default: null
        description: Similarity threshold percentage for structure search (70-100). Only used with SMILES search
      smiles:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: SMILES structure for similarity search. Requires similarity_threshold parameter
    required:
    - name
    type: object
  drug_search:
    additionalProperties: false
    properties:
      drug_name:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Drug name or partial name (e.g., 'imatinib')
      indication:
        description: Disease indication to search for (e.g., 'hypertension', 'cancer', 'diabetes'). This is the primary search criterion.
        type: string
      limit:
        default: 20
        description: Maximum number of results to return
        maximum: 1000
        minimum: 1
        type: integer
      max_phase:
        anyOf:
        - enum:
          - 0
          - 1
          - 2
          - 3
          - 4
          type: integer
        - type: 'null'
        default: null
        description: Filter by clinical phase. Use 4 for approved drugs, 3 for late-stage trials
      molecule_chembl_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: ChEMBL identifier for the molecule (e.g., 'CHEMBL941')
      only_approved:
        default: false
        description: If True, returns only approved drugs (max_phase=4). Shortcut for max_phase=4
        type: boolean
    required:
    - indication
    type: object
  get_admet:
    additionalProperties: false
    properties:
      molecule_chembl_id:
        description: ChEMBL identifier for the molecule (e.g., 'CHEMBL941' for imatinib). If you only have a drug name, use compound_search first to find the ChEMBL ID.
        type: string
    required:
    - molecule_chembl_id
    type: object
  get_bioactivity:
    additionalProperties: false
    properties:
      activity_type:
        anyOf:
        - enum:
          - IC50
          - EC50
          - Ki
          - Kd
          - AC50
          - GI50
          - ED50
          - Potency
          type: string
        - type: 'null'
        default: null
        description: Activity type to filter. IC50 (inhibitors), Ki/Kd (binding affinity), EC50 (agonists)
      limit:
        default: 20
        description: Maximum number of results to return
        maximum: 1000
        minimum: 1
        type: integer
      max_value:
        anyOf:
        - type: number
        - type: 'null'
        default: null
        description: 'Maximum activity value for filtering (in standard units). Example: max_value=100 with unit=''nM'' finds potent compounds'
      min_pchembl:
        anyOf:
        - maximum: 14
          minimum: 0
          type: number
        - type: 'null'
        default: null
        description: 'Minimum pChEMBL value (higher = more potent). pChEMBL = -log(molar IC50/Ki/Kd/EC50). Typical range: 4-10'
      min_value:
        anyOf:
        - type: number
        - type: 'null'
        default: null
        description: Minimum activity value for filtering (in standard units)
      molecule_chembl_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: ChEMBL identifier for the molecule (e.g., 'CHEMBL25')
      target_chembl_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: ChEMBL identifier for the target (e.g., 'CHEMBL1824'). Use target_search tool first to find this ID.
      unit:
        anyOf:
        - enum:
          - nM
          - uM
          - mM
          - pM
          - M
          type: string
        - type: 'null'
        default: null
        description: Activity unit for value filtering. nM is most common for potent compounds
    type: object
  get_mechanism:
    additionalProperties: false
    properties:
      action_type:
        anyOf:
        - enum:
          - INHIBITOR
          - AGONIST
          - ANTAGONIST
          - BLOCKER
          - MODULATOR
          - OPENER
          - ACTIVATOR
          - POSITIVE ALLOSTERIC MODULATOR
          - NEGATIVE ALLOSTERIC MODULATOR
          - PARTIAL AGONIST
          - INVERSE AGONIST
          type: string
        - type: 'null'
        default: null
        description: Type of action. INHIBITOR is most common for small molecules
      limit:
        default: 20
        description: Maximum number of results to return
        maximum: 1000
        minimum: 1
        type: integer
      molecule_chembl_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: ChEMBL identifier for the molecule (e.g., 'CHEMBL25')
      target_chembl_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: ChEMBL identifier for the target (e.g., 'CHEMBL1824')
    type: object
  target_search:
    additionalProperties: false
    properties:
      gene_symbol:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Gene symbol to search for (e.g., 'EGFR', 'BRAF', 'TP53'). Searches target component synonyms.
      limit:
        default: 20
        description: Maximum number of results to return
        maximum: 1000
        minimum: 1
        type: integer
      organism:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Organism name (e.g., 'Homo sapiens', 'human', 'mouse')
      target_chembl_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: ChEMBL identifier for the target (e.g., 'CHEMBL1824')
      target_name:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Target name or partial name (e.g., 'EGFR', 'kinase', 'receptor')
      target_type:
        anyOf:
        - enum:
          - SINGLE PROTEIN
          - PROTEIN COMPLEX
          - PROTEIN FAMILY
          - ORGANISM
          - TISSUE
          - CELL-LINE
          - NUCLEIC-ACID
          - SUBCELLULAR
          type: string
        - type: 'null'
        default: null
        description: Type of target. SINGLE PROTEIN recommended for highest confidence data
    type: object
action_descriptions:
  compound_search: |-
    Search for chemical compounds in ChEMBL database by name, ChEMBL ID, or molecular structure.

    WHEN TO USE compound_search vs drug_search:
    • compound_search: Use for ANY molecule lookup by name, ID, or structure (may lack clinical data)
    • drug_search: Use ONLY when searching by therapeutic indication (e.g., "drugs for diabetes")
    For a simple drug name lookup like "find aspirin", use compound_search.

    SEARCH STRATEGIES:
    • By name: Use 'name' for drug names, synonyms, or trade names (case-insensitive, partial match)
    • By ID: Use 'chembl_id' for direct lookup when you know the exact identifier (e.g., 'CHEMBL25' for aspirin)
    • By structure: Use 'smiles' with 'similarity_threshold' (70-100%) for finding structurally similar compounds
    • Substructure: Use 'smiles' without threshold to find compounds containing that substructure

    SIMILARITY SEARCH (uses Morgan fingerprints, radius 2, 2048 bits via FPSim2):
    • 70%: Loose similarity (finds diverse analogs)
    • 80%: Good starting threshold (finds close analogs)
    • 90%+: Very strict (finds nearly identical compounds)

    MAX_PHASE VALUES (clinical development stage):
    • 4 = Approved (marketed drug, e.g., FDA/EMA approved)
    • 3 = Phase 3 Clinical Trials
    • 2 = Phase 2 Clinical Trials (includes INN applications)
    • 1 = Phase 1 Clinical Trials (includes USAN applications)
    • 0.5 = Early Phase 1
    • -1 = Unknown clinical phase
    • NULL = Preclinical compound (bioactivity data only)

    RETURNED DATA INCLUDES:
    • Molecular properties: MW, ALogP (lipophilicity), PSA, HBA, HBD, rotatable bonds, aromatic rings
    • QED (Quantitative Estimate of Drug-likeness): 0-1 scale, higher = more drug-like
    • Chirality: 0=racemic, 1=single stereoisomer, 2=achiral, -1=unchecked
    • Rule of Five: MW<500, ALogP<5, HBD<5, HBA<10 (compounds with 0-1 violations are Ro5 compliant)
    • ATC classifications, cross-references to external databases, molecule hierarchy (parent/salt forms)

    TIPS:
    • For approved drugs, add max_phase=4
    • ChEMBL uses compound 'families' - salts map to parent compounds
    • Use get_bioactivity after finding compounds to get their target activities
    • Properties are calculated on parent (salt-free) form

    EXAMPLES:
    • Find aspirin: name='aspirin' or chembl_id='CHEMBL25'
    • Find kinase inhibitors: name='nib', max_phase=4
    • Find structural analogs: Use SMILES with similarity_threshold=80
  drug_search: |-
    Search for approved drugs and clinical candidates by therapeutic indication.

    PURPOSE: Find drugs used for specific diseases, identify approved treatments, explore drug repurposing opportunities.

    DRUG VS COMPOUND:
    • Drug: Compound with assigned INN/USAN name + clinical data (max_phase ≥ 1)
    • Compound: Any molecule in ChEMBL (may only have bioactivity data)
    • Use compound_search for broader chemical searches, drug_search for therapeutic applications

    MAX_PHASE VALUES (clinical development stage):
    • 4 = Approved (marketed drug, e.g., FDA/EMA approved)
    • 3 = Phase III Clinical Trials (large-scale efficacy trials)
    • 2 = Phase II Clinical Trials (proof of concept, includes INN applications)
    • 1 = Phase I Clinical Trials (safety, includes USAN applications)
    • 0.5 = Early Phase 1 (exploratory studies)
    • -1 = Unknown clinical phase (status uncertain)
    • NULL = Preclinical only (no human trials, compound_search more appropriate)

    SAFETY FLAGS IN RESULTS:
    • black_box_warning: 1 = has FDA black box warning (serious safety concern)
    • withdrawn_flag: true = withdrawn from one or more markets
    • withdrawn_reason: Why drug was withdrawn (e.g., hepatotoxicity, cardiac effects)
    • withdrawn_country/year/class: Details about market withdrawal

    INDICATION SEARCH:
    • Uses MeSH (Medical Subject Headings) disease terminology
    • Also searches EFO (Experimental Factor Ontology) terms
    • Partial matching supported (e.g., 'cancer' matches 'breast cancer', 'lung cancer')
    • Common indications: hypertension, diabetes, cancer, asthma, depression, arthritis

    INDICATION DETAILS IN RESULTS:
    • mesh_id/mesh_heading: MeSH disease classification
    • efo_id/efo_term: EFO disease ontology
    • max_phase_for_ind: Highest phase achieved for this specific indication

    TIPS:
    • Use only_approved=True for marketed drugs only
    • Check black_box_warning and withdrawn_flag for safety information
    • After finding drugs, use get_mechanism to understand their molecular targets
    • Use max_phase=3 to include drugs in late-stage trials (potential future approvals)

    WORKFLOW EXAMPLE:
    1. drug_search(indication='hypertension', only_approved=True) → Find approved antihypertensives
    2. get_mechanism(molecule_chembl_id='CHEMBL1200749') → Get targets for amlodipine
    3. get_bioactivity(target_chembl_id='CHEMBL1940') → Find other L-type calcium channel blockers
  get_admet: |-
    Retrieve ADMET-related molecular properties for drug-likeness assessment.

    IMPORTANT: ChEMBL provides CALCULATED molecular properties (from structure), not experimental ADMET data.
    For experimental ADMET measurements, use get_bioactivity with specific ADMET target ChEMBL IDs.

    CALCULATED PROPERTIES (what this tool returns):
    • ALogP: Calculated lipophilicity (Wildman-Crippen LogP)
      - Optimal for oral drugs: 1-3
      - < 0: Poor membrane permeability
      - > 5: Poor solubility, potential accumulation
    • Molecular Weight (full_mwt): Total molecular weight including salts
    • Molecular Weight (mw_freebase): Parent compound molecular weight only
    • H-bond Donors (HBD): Rule-of-5 limit < 5
    • H-bond Acceptors (HBA): Rule-of-5 limit < 10
    • Polar Surface Area (PSA): Topological PSA
      - < 140 Å²: Good oral absorption
      - < 90 Å²: Better CNS penetration (crosses BBB)
    • Rotatable Bonds (RTB): < 10 for good oral bioavailability
    • Heavy Atoms: Non-hydrogen atom count
    • Aromatic Rings: < 4 recommended for drug-likeness
    • Rule-of-5 Violations (num_ro5_violations): 0-1 preferred
    • Rule-of-3 Pass (ro3_pass): Y/N for fragment-like properties
    • QED Weighted: Quantitative Estimate of Drug-likeness (0-1 scale)
      - Higher = more drug-like profile
      - Based on MW, ALogP, HBD, HBA, PSA, RTB, aromatic rings, alerts

    DRUG-LIKENESS GUIDELINES:
    • Lipinski Rule-of-5: MW < 500, ALogP < 5, HBD ≤ 5, HBA ≤ 10
    • Veber Rules: PSA ≤ 140 Å², RTB ≤ 10
    • Lead-like: MW < 450, ALogP -4 to 4.2, RTB ≤ 10

    FOR EXPERIMENTAL ADMET DATA (use get_bioactivity with these targets):
    • hERG (CHEMBL240): Cardiac safety (K+ channel, IC50 > 10µM preferred)
    • CYP3A4 (CHEMBL340): Major metabolizing enzyme (avoid strong inhibition)
    • CYP2D6 (CHEMBL289): Polymorphic CYP (genetic variability concerns)
    • CYP2C9 (CHEMBL3397): Warfarin metabolism (drug interactions)
    • P-glycoprotein (CHEMBL4302): Drug efflux transporter (affects BBB, gut)

    WORKFLOW EXAMPLE:
    1. Get calculated properties: get_admet(molecule_chembl_id='CHEMBL941')
    2. Check hERG liability: get_bioactivity(molecule_chembl_id='CHEMBL941', target_chembl_id='CHEMBL240')
    3. Check CYP inhibition: get_bioactivity(molecule_chembl_id='CHEMBL941', target_chembl_id='CHEMBL340')
  get_bioactivity: |-
    Retrieve bioactivity measurements (IC50, Ki, EC50, etc.) for compound-target interactions.

    WORKFLOW: Use target_search or compound_search first to get ChEMBL IDs, then query bioactivity.

    ACTIVITY TYPES (standard_type field):
    • IC50: Half-maximal inhibitory concentration (most common for inhibitors)
    • Ki: Inhibition constant (binding affinity, independent of substrate concentration)
    • Kd: Dissociation constant (direct binding measurement)
    • EC50: Half-maximal effective concentration (for agonists)
    • AC50: Half-maximal activity concentration
    • Potency/ED50: Effective dose measurements

    pChEMBL VALUE (recommended for filtering - standardized potency):
    • Definition: -log10(molar IC50/Ki/Kd/EC50/AC50/Potency/ED50)
    • Only calculated when: standard_relation='=' AND standard_units='nM' AND value>0
    • pChEMBL 9 = 1 nM (highly potent, drug-like)
    • pChEMBL 7 = 100 nM (potent)
    • pChEMBL 6 = 1 µM (moderate)
    • pChEMBL 5 = 10 µM (weak)
    • pChEMBL 3 = 1 mM (very weak)
    • Use min_pchembl >= 6 for µM or better activity
    • Use min_pchembl >= 7 for sub-100nM potency (drug-like)

    ASSAY TYPES (assay_type field):
    • B (Binding): Direct target binding measurements (Ki, Kd)
    • F (Functional): Biological effect in cells/tissues (EC50, IC50 in cellular context)
    • A (ADME): Absorption, distribution, metabolism, excretion assays (t1/2, bioavailability)
    • T (Toxicity): Cytotoxicity, hERG inhibition
    • P (Physicochemical): Solubility, stability (no biological material)
    • U (Unclassified): Cannot fit single category

    DATA VALIDITY FLAGS (data_validity_comment field):
    • 'Outside typical range': Value unusually high/low for activity type
    • 'Potential missing data': Incomplete data entry
    • 'Potential author error': Suspected error in publication
    • 'Manually validated': Curator confirmed accuracy
    • 'Potential transcription error': Values differ by 3 or 6 orders of magnitude (unit error)

    LIGAND EFFICIENCY METRICS (in results when available):
    • LE (Ligand Efficiency): Activity per heavy atom
    • BEI (Binding Efficiency Index): Activity per molecular weight
    • LLE (Lipophilic Ligand Efficiency): pActivity - LogP
    • SEI (Surface Efficiency Index): Activity per polar surface area

    TIPS:
    • Use with ADMET targets to get experimental ADMET data
    • Combine molecule_chembl_id + target_chembl_id for specific compound-target pairs
    • Check potential_duplicate flag - may indicate cited (not independent) measurements
    • activity_comment may indicate 'active'/'inactive' conclusions from depositor
    • document_chembl_id links to source publication for verification
  get_mechanism: |-
    Retrieve mechanism of action (MoA) data for approved drugs and clinical candidates.

    PURPOSE: Understand how drugs interact with their targets - essential for drug repurposing, understanding polypharmacology, and target validation.

    ACTION TYPES (action_type field):
    • INHIBITOR: Blocks target activity (most common for small molecule drugs)
    • ANTAGONIST: Blocks receptor activation (prevents agonist binding)
    • AGONIST: Activates receptor (mimics natural ligand)
    • BLOCKER: Blocks ion channels or transporters
    • MODULATOR: Alters target activity (often allosteric mechanism)
    • POSITIVE ALLOSTERIC MODULATOR: Enhances agonist response without binding orthosteric site
    • NEGATIVE ALLOSTERIC MODULATOR: Reduces agonist response
    • OPENER: Opens ion channels (increases conductance)
    • ACTIVATOR: Increases enzyme activity
    • PARTIAL AGONIST: Partially activates receptor (submaximal efficacy)
    • INVERSE AGONIST: Reduces constitutive (basal) receptor activity
    • SUBSTRATE: Acts as substrate for enzyme (e.g., prodrugs)
    • RELEASING AGENT: Causes release of neurotransmitters
    • SEQUESTERING AGENT: Binds and removes target (e.g., antibodies)

    KEY RESULT FIELDS:
    • direct_interaction: true = drug binds directly to target; false = indirect effect
    • disease_efficacy: true = target is directly relevant to therapeutic effect
    • molecular_mechanism: Specific molecular action (e.g., 'Cyclooxygenase inhibitor')
    • binding_site_name/comment: Where drug binds on target (e.g., 'ATP binding site')
    • selectivity_comment: Notes on target selectivity vs related proteins
    • mechanism_refs: Literature references supporting the mechanism

    BINDING SITE INFORMATION:
    Results may include binding site details when known:
    • site_name: Named binding pocket (e.g., 'Colchicine site', 'ATP binding domain')
    • site_id: ChEMBL binding site identifier for further lookup

    WORKFLOW:
    1. Find compound: compound_search(name='imatinib')
    2. Get mechanisms: get_mechanism(molecule_chembl_id='CHEMBL941')
    3. Validate with bioactivity: get_bioactivity(molecule_chembl_id='CHEMBL941', target_chembl_id='CHEMBL1862')

    TIPS:
    • MoA data is manually curated for approved drugs and advanced clinical candidates
    • Use target_chembl_id to find all drugs acting on a specific target (drug repurposing)
    • Check mechanism_refs for original publications supporting the mechanism
    • Combine with get_bioactivity to see quantitative potency data for mechanism targets
    • Parent compound IDs work even when mechanism is stored under salt form
  target_search: |-
    Search for biological targets (proteins, enzymes, receptors, organisms) in ChEMBL database.

    SEARCH STRATEGIES:
    • By name: Use 'target_name' for protein names, families (e.g., 'kinase'), or receptors
    • By gene: Use 'gene_symbol' for exact gene symbol matches (e.g., 'EGFR', 'BRAF', 'TP53')
    • By ID: Use 'target_chembl_id' for direct lookup (e.g., 'CHEMBL203' for EGFR)
    • By organism: Filter results to specific species (e.g., 'Homo sapiens', 'Mus musculus')
    • By type: Filter by target_type to get specific categories

    TARGET TYPES (target_type field):
    • SINGLE PROTEIN: Individual protein (most common, highest confidence bioactivity data)
    • PROTEIN COMPLEX: Multi-subunit complex (e.g., ion channels, GPCRs with multiple subunits)
    • PROTEIN FAMILY: Homologous protein groups (broader search, lower specificity)
    • PROTEIN-PROTEIN INTERACTION: Two interacting proteins
    • CHIMERIC PROTEIN: Engineered fusion protein
    • SELECTIVITY GROUP: Panel of related targets for selectivity profiling
    • ORGANISM: Whole organism (bacteria, parasites, viruses, fungi)
    • TISSUE: Tissue-level target
    • CELL-LINE: Cell-based target (phenotypic screening)
    • NUCLEIC-ACID: DNA/RNA targets
    • SUBCELLULAR: Subcellular compartments
    • UNKNOWN: Unclassified targets

    TARGET CONFIDENCE SCORES (in bioactivity data):
    • 9: Direct single protein target (highest confidence, most reliable)
    • 8: Homologous single protein (inferred from related species)
    • 7: Direct protein complex subunits (multi-subunit target)
    • 6: Homologous protein complex
    • 5: Direct protein selectivity group
    • 4: Homologous selectivity group
    • 3: Protein not in target complex
    • 2: Non-protein organism target
    • 1: Non-molecular target (cell-line, organism, tissue)
    • 0: Default or uncurated

    TARGET RELATIONSHIPS (between targets):
    • EQUIVALENT TO: Same target in different contexts
    • OVERLAPS WITH: Partially shared components
    • SUBSET OF: Contains subset of components
    • SUPERSET OF: Contains additional components

    RETURNED DATA INCLUDES:
    • Target components: Protein subunits with accessions (UniProt)
    • GO annotations: Gene Ontology terms for molecular function, biological process, cellular component
    • Cross-references: Links to UniProt, PFAM, InterPro, IntAct, Reactome, etc.
    • Species information: Tax ID and organism name

    TIPS:
    • Use gene_symbol for exact matches - more precise than target_name
    • After finding a target, use get_bioactivity with target_chembl_id to find active compounds
    • For drug discovery, focus on SINGLE PROTEIN targets (confidence >= 7)
    • Filter by organism='Homo sapiens' for human targets
    • Check GO annotations to understand target function and localization
---
# ChEMBL

An MCP connector for the manually curated ChEMBL database of bioactive, drug-like small
molecules. Supports drug-discovery research: compound screening, target identification,
mechanism analysis, and pharmacokinetic/safety profiling.

## Tools

### compound_search
Find compounds by name, SMILES, or ChEMBL ID. Starting point for compound analysis.

### target_search
Find biological targets (proteins, genes, receptors) by gene symbol or protein name.

### get_bioactivity
Retrieve quantitative activity data (IC50, EC50, Ki) for compound–target pairs.

### get_mechanism
Get a drug's mechanism of action and its primary target binding.

### drug_search
Find approved drugs by name or indication, for therapeutic-landscape analysis.

### get_admet
Retrieve ADMET (absorption, distribution, metabolism, excretion, toxicity) properties.

## Typical workflow

1. `compound_search` or `drug_search` to identify the compound of interest.
2. `get_mechanism` to understand how it works and its primary targets.
3. `target_search` for detailed target information.
4. `get_bioactivity` for quantitative binding/activity data.
5. `get_admet` for pharmacokinetic properties.
