---
name: clinical_trials_connector
description: ClinicalTrials.gov (API v2) — search and analyze FDA-regulated clinical studies by condition, intervention, sponsor, investigator, eligibility, and endpoints.
version: 1.0.0
type: worker
permission_mode: read_only
featured: true
connection:
  transport: streamable_http
  url: https://hcls.mcp.claude.com/clinical_trials/mcp
actions:
  - search_trials
  - get_trial_details
  - search_by_sponsor
  - search_investigators
  - analyze_endpoints
  - search_by_eligibility
action_schemas:
  analyze_endpoints:
    additionalProperties: false
    properties:
      condition:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Disease or therapeutic area for aggregate analysis. Examples:
          ''diabetes'', ''Alzheimer'', ''breast cancer'', ''heart failure''. Required
          for aggregate analysis, optional if nct_id is provided.'
      nct_id:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'NCT ID for single trial analysis. Example: ''NCT03661411''. If
          provided, analyzes only this trial''s endpoints. Either nct_id or condition
          must be provided.'
      page_size:
        default: 50
        description: Number of trials to analyze. Default 50. Use 100-200 for comprehensive
          analysis, 20-30 for quick overview. More trials = more representative but
          slower.
        maximum: 1000
        minimum: 1
        type: integer
      phase:
        anyOf:
        - type: string
        - items:
            enum:
            - EARLY_PHASE1
            - PHASE1
            - PHASE2
            - PHASE3
            - PHASE4
            - NA
            type: string
          type: array
        - type: 'null'
        default: null
        description: 'Filter by trial phase. Endpoints often differ by phase:
  
          - PHASE1/PHASE2: Often safety endpoints, biomarkers
  
          - PHASE3: Pivotal efficacy endpoints (most relevant for regulatory)
  
          - PHASE4: Real-world outcomes
  
          Example: [''PHASE3''] for registration-quality endpoints'
      start_date_after:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Only analyze trials started after this date. Format: YYYY-MM-DD.
          Useful for seeing modern/recent endpoint trends. Example: ''2020-01-01'' for
          trials from 2020 onwards.'
    type: object
  get_trial_details:
    additionalProperties: false
    properties:
      nct_id:
        description: 'NCT identifier for the clinical trial. Format: ''NCT'' + 8 digits.
          Examples: ''NCT04567890'', ''NCT00001234''. If user provides just the number,
          prepend ''NCT''. Case-insensitive.'
        type: string
    required:
    - nct_id
    type: object
  search_by_eligibility:
    additionalProperties: false
    properties:
      condition:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Primary medical condition for the patient. Optional. Examples:
          ''diabetes'', ''breast cancer'', ''Alzheimer'', ''heart failure''. Can be
          omitted if searching by other eligibility criteria only.'
      eligibility_keywords:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Keywords to search in inclusion/exclusion criteria text. Examples:
          ''HbA1c > 8'', ''BRCA mutation'', ''ECOG 0-1'', ''treatment naive'', ''prior
          chemotherapy''. Searches the full eligibility criteria text for these terms.'
      max_age:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Patient''s age (upper bound for matching). Format: ''X Years''
          or ''X Months''. Examples: ''75 Years'', ''12 Years''. Finds trials where
          the trial''s MaximumAge requirement is at or above this value, meaning the
          patient meets the maximum age requirement.'
      min_age:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Patient''s age (lower bound for matching). Format: ''X Years''
          or ''X Months''. Examples: ''18 Years'', ''65 Years'', ''6 Months''. Finds
          trials where the trial''s MinimumAge requirement is at or below this value,
          meaning the patient meets the minimum age requirement.'
      page_size:
        default: 10
        description: Results per page. Default 10.
        maximum: 1000
        minimum: 1
        type: integer
      page_token:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Pagination token from previous response.
      sex:
        anyOf:
        - enum:
          - ALL
          - MALE
          - FEMALE
          type: string
        - type: 'null'
        default: null
        description: 'Patient''s sex for eligibility matching:
  
          - MALE: Find trials accepting male patients
  
          - FEMALE: Find trials accepting female patients
  
          - ALL: No sex restriction (default behavior if not specified)'
      status:
        anyOf:
        - type: string
        - items:
            enum:
            - NOT_YET_RECRUITING
            - RECRUITING
            - ENROLLING_BY_INVITATION
            - ACTIVE_NOT_RECRUITING
            - COMPLETED
            - SUSPENDED
            - TERMINATED
            - WITHDRAWN
            - AVAILABLE
            - NO_LONGER_AVAILABLE
            - TEMPORARILY_NOT_AVAILABLE
            - APPROVED_FOR_MARKETING
            - WITHHELD
            - UNKNOWN
            type: string
          type: array
        - type: 'null'
        default: null
        description: Trial recruitment status. Defaults to ['RECRUITING'] if not specified.
          For patient matching, usually want RECRUITING trials only. Add NOT_YET_RECRUITING
          to include upcoming trials.
    type: object
  search_by_sponsor:
    additionalProperties: false
    properties:
      condition:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Filter by disease/condition to focus on a therapeutic area. Examples:
          ''cancer'', ''diabetes'', ''COVID-19'', ''Alzheimer''. Useful for questions
          like ''What is Pfizer doing in oncology?'''
      count_total:
        default: false
        description: Set true to get total count. Useful for 'How many trials does X
          sponsor?'
        type: boolean
      page_size:
        default: 10
        description: Results per page. Use 50-100 for comprehensive pipeline analysis.
        maximum: 1000
        minimum: 1
        type: integer
      page_token:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Pagination token from previous response to get next page.
      phase:
        anyOf:
        - type: string
        - items:
            enum:
            - EARLY_PHASE1
            - PHASE1
            - PHASE2
            - PHASE3
            - PHASE4
            - NA
            type: string
          type: array
        - type: 'null'
        default: null
        description: 'Filter by development phase to analyze pipeline maturity:
  
          - PHASE1: Early development, safety focus
  
          - PHASE2: Mid-stage, efficacy testing
  
          - PHASE3: Late-stage, large trials before approval
  
          - PHASE4: Post-approval studies
  
          Select multiple for broader view: [''PHASE2'', ''PHASE3'']'
      sponsor_name:
        description: 'Company or organization name. Examples: ''Pfizer'', ''Moderna'',
          ''Novartis'', ''NIH'', ''Mayo Clinic''. Partial matches work (e.g., ''Pfizer''
          finds ''Pfizer Inc'' and ''Pfizer Pharmaceuticals'').'
        type: string
      status:
        anyOf:
        - type: string
        - items:
            enum:
            - NOT_YET_RECRUITING
            - RECRUITING
            - ENROLLING_BY_INVITATION
            - ACTIVE_NOT_RECRUITING
            - COMPLETED
            - SUSPENDED
            - TERMINATED
            - WITHDRAWN
            - AVAILABLE
            - NO_LONGER_AVAILABLE
            - TEMPORARILY_NOT_AVAILABLE
            - APPROVED_FOR_MARKETING
            - WITHHELD
            - UNKNOWN
            type: string
          type: array
        - type: 'null'
        default: null
        description: 'Filter by trial status:
  
          - RECRUITING: Currently enrolling (active development)
  
          - COMPLETED: Finished trials (historical data)
  
          - ACTIVE_NOT_RECRUITING: Ongoing but closed to enrollment
  
          - TERMINATED: Stopped early (may indicate issues)'
    required:
    - sponsor_name
    type: object
  search_investigators:
    additionalProperties: false
    properties:
      condition:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Disease or therapeutic area to search. Optional. Examples: ''Alzheimer'',
          ''breast cancer'', ''diabetes'', ''heart failure''. Finds investigators running
          trials in this area. Can be omitted if searching by institution or location
          only.'
      institution:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Institution or facility name to filter by. Examples: ''Mayo Clinic'',
          ''Duke University'', ''MD Anderson'', ''Johns Hopkins''. Takes precedence
          over location if both specified.'
      investigator_name:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Direct search by investigator name. Examples: ''Smith'', ''John
          Smith'', ''Dr. Chen''. Searches in OverallOfficialName and ResponsiblePartyInvestigatorFullName
          fields. Use this when you know a specific investigator''s name.'
      location:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Geographic location to filter by. Examples: ''Boston'', ''California'',
          ''United States'', ''Germany''. Use when institution is not specified.'
      page_size:
        default: 20
        description: Number of trials to analyze. More trials = more investigators found.
          Default 20. Use 50-100 for comprehensive investigator discovery.
        maximum: 1000
        minimum: 1
        type: integer
      status:
        anyOf:
        - type: string
        - items:
            enum:
            - NOT_YET_RECRUITING
            - RECRUITING
            - ENROLLING_BY_INVITATION
            - ACTIVE_NOT_RECRUITING
            - COMPLETED
            - SUSPENDED
            - TERMINATED
            - WITHDRAWN
            - AVAILABLE
            - NO_LONGER_AVAILABLE
            - TEMPORARILY_NOT_AVAILABLE
            - APPROVED_FOR_MARKETING
            - WITHHELD
            - UNKNOWN
            type: string
          type: array
        - type: 'null'
        default: null
        description: Filter by trial status. Default searches all statuses. Use ['RECRUITING']
          to find currently active investigators.
    type: object
  search_trials:
    additionalProperties: false
    properties:
      advanced_query:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Advanced query using Essie expression syntax. Only use when basic
          parameters are insufficient. Syntax: AREA[FieldName]value or AREA[FieldName]RANGE[min,max].
          Examples:
  
          - Date filter: ''AREA[StartDate]RANGE[2023-01-01,MAX]'' (trials started after
          Jan 2023)
  
          - Enrollment: ''AREA[EnrollmentCount]RANGE[100,MAX]'' (100+ participants)
  
          - List size: ''AREA[Phase:size]2'' (trials with exactly 2 phases)
  
          - Geo proximity: Use filter.geo parameter instead for distance-based searches'
      condition:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Disease or condition to search. This is the most common search
          parameter. Examples: ''diabetes'', ''lung cancer'', ''Alzheimer'', ''COVID-19''.
          Supports Boolean operators: ''diabetes AND neuropathy'', ''cancer NOT skin''.
          Use quotes for exact phrases: ''"type 2 diabetes"''. Medical synonyms are
          automatically included in search.'
      count_total:
        default: false
        description: Set to true to get total count of matching trials. Useful for questions
          like 'How many trials exist for X?'. Slightly slower.
        type: boolean
      intervention:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Drug, treatment, or intervention name. Examples: ''pembrolizumab'',
          ''metformin'', ''CAR-T therapy'', ''radiation''. Use OR for alternatives:
          ''aspirin OR ibuprofen OR naproxen''. Brand and generic names both work.'
      location:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Geographic location for trial sites. Can be city, state, country,
          or region. Examples: ''Boston'', ''California'', ''United States'', ''Germany'',
          ''Europe''. Useful for finding trials patients can physically access.'
      page_size:
        default: 10
        description: Number of results per page. Default 10. Use 50-100 for comprehensive
          searches, 5-10 for quick lookups.
        maximum: 1000
        minimum: 1
        type: integer
      page_token:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: Token from previous response's next_page_token to get next page.
          Do not set for first request.
      phase:
        anyOf:
        - type: string
        - items:
            enum:
            - EARLY_PHASE1
            - PHASE1
            - PHASE2
            - PHASE3
            - PHASE4
            - NA
            type: string
          type: array
        - type: 'null'
        default: null
        description: 'Filter by trial phase. Values:
  
          - EARLY_PHASE1: Initial safety testing
  
          - PHASE1: Safety and dosage testing in small groups
  
          - PHASE2: Efficacy and side effects testing
  
          - PHASE3: Large-scale efficacy confirmation
  
          - PHASE4: Post-market surveillance
  
          - NA: Not applicable (observational studies)
  
          Can select multiple: [''PHASE2'', ''PHASE3'']'
      sponsor:
        anyOf:
        - type: string
        - type: 'null'
        default: null
        description: 'Organization sponsoring or funding the trial. Examples: ''Pfizer'',
          ''NIH'', ''Novartis'', ''Mayo Clinic''. For comprehensive sponsor analysis,
          use search_by_sponsor tool instead.'
      status:
        anyOf:
        - type: string
        - items:
            enum:
            - NOT_YET_RECRUITING
            - RECRUITING
            - ENROLLING_BY_INVITATION
            - ACTIVE_NOT_RECRUITING
            - COMPLETED
            - SUSPENDED
            - TERMINATED
            - WITHDRAWN
            - AVAILABLE
            - NO_LONGER_AVAILABLE
            - TEMPORARILY_NOT_AVAILABLE
            - APPROVED_FOR_MARKETING
            - WITHHELD
            - UNKNOWN
            type: string
          type: array
        - type: 'null'
        default: null
        description: 'Filter by recruitment status. Common values:
  
          - RECRUITING: Actively enrolling patients (use this to find trials patients
          can join)
  
          - COMPLETED: Trial finished (use for historical research)
  
          - ACTIVE_NOT_RECRUITING: Ongoing but not enrolling
  
          - NOT_YET_RECRUITING: Approved but not started
  
          - TERMINATED/WITHDRAWN: Stopped early
  
          Can select multiple: [''RECRUITING'', ''NOT_YET_RECRUITING'']'
      study_type:
        anyOf:
        - enum:
          - INTERVENTIONAL
          - OBSERVATIONAL
          - EXPANDED_ACCESS
          type: string
        - type: 'null'
        default: null
        description: 'Type of clinical study:
  
          - INTERVENTIONAL: Tests a treatment/intervention (most common for drug trials)
  
          - OBSERVATIONAL: Observes outcomes without intervention
  
          - EXPANDED_ACCESS: Provides experimental treatment outside trials'
    type: object
action_descriptions:
  analyze_endpoints: 'Analyze primary and secondary outcome measures (endpoints) from
    clinical trials.
  
  
    MODES (provide ONLY nct_id OR condition, not both):
  
    1. Single Trial: Provide nct_id ONLY to analyze one specific trial''s endpoints
  
    2. Aggregate: Provide condition ONLY to analyze patterns across multiple trials
  
    If both provided, nct_id takes precedence (single trial mode).
  
  
    SINGLE TRIAL MODE (nct_id):
  
    - Returns all endpoints for the specified trial
  
    - Useful for understanding specific trial design
  
    - Example: nct_id=''NCT03661411''
  
  
    AGGREGATE MODE (condition):
  
    - Analyzes endpoints across many trials in a therapeutic area
  
    - Identifies common endpoint patterns and measures
  
    - Useful for protocol design and competitive analysis
  
    - Example: condition=''diabetes'', phase=[''PHASE3'']
  
  
    WHAT THIS RETURNS:
  
    - List of primary endpoints (main efficacy measures)
  
    - List of secondary endpoints (additional outcomes)
  
    - List of other endpoints (exploratory outcomes)
  
    - Most common measures across analyzed trials
  
    - Timeframes for each endpoint measurement
  
  
    EXAMPLES:
  
    - Single trial: nct_id=''NCT03661411''
  
    - Phase 3 cancer endpoints: condition=''cancer'', phase=[''PHASE3'']
  
    - Recent diabetes outcomes: condition=''diabetes'', start_date_after=''2022-01-01'''
  get_trial_details: 'Get comprehensive details for a specific clinical trial using
    its NCT ID.
  
  
    WHEN TO USE:
  
    - User provides a specific NCT ID (e.g., ''Tell me about NCT04567890'')
  
    - Need full eligibility criteria, endpoints, or locations for a specific trial
  
    - Following up on a trial found via search_trials
  
    - Answering detailed questions about a known trial
  
    - Verifying patient eligibility for a specific trial
  
  
    USE search_trials INSTEAD FOR:
  
    - Finding trials (this tool requires knowing the NCT ID)
  
    - Browsing trials by condition/intervention/sponsor
  
  
    WHAT THIS RETURNS:
  
    - Full eligibility criteria (inclusion/exclusion)
  
    - Study design and methodology
  
    - Primary, secondary, and other endpoints with timeframes
  
    - All study locations with contact info
  
    - Sponsor and collaborator details
  
    - Study dates and enrollment numbers
  
    - Results link if trial has published results
  
  
    NCT ID FORMAT: ''NCT'' followed by 8 digits (e.g., NCT04567890, NCT00001234)'
  search_by_eligibility: 'Find clinical trials matching specific patient eligibility
    criteria. Use this for patient-trial matching and finding trials a specific patient
    might qualify for.
  
  
    DEFAULT STATUS: Only searches RECRUITING trials. To include completed, upcoming,
    or all trials, explicitly set the status parameter.
  
  
    WHEN TO USE:
  
    - Patient matching: ''Find trials for a 65-year-old female with diabetes''
  
    - Specific criteria: ''Trials requiring HbA1c > 8%'' or ''BRCA positive trials''
  
    - Age-restricted searches: ''Pediatric cancer trials'' or ''Trials for elderly patients''
  
    - Finding trials by inclusion/exclusion criteria
  
  
    USE search_trials FOR:
  
    - General disease/condition searches
  
    - When patient demographics don''t matter
  
  
    ELIGIBILITY KEYWORDS TIPS:
  
    - Use medical abbreviations: ''ECOG'', ''HbA1c'', ''BMI'', ''eGFR''
  
    - Search criteria text: ''prior chemotherapy'', ''treatment naive''
  
    - Biomarkers: ''BRCA mutation'', ''HER2 positive'', ''PD-L1''
  
    - Lab values: ''creatinine'', ''ALT'', ''bilirubin''
  
  
    - ICD-10 codes indicate specific subtypes (E10.x=Type 1, E11.x=Type 2, etc.)
  
    - Disease subtypes matter: Type 1 vs Type 2 diabetes, HER2+ vs HER2- cancer, etc.
  
    EXAMPLES:
  
    - ''65yo diabetic patient'' -> condition=''diabetes'', min_age=''18 Years'', max_age=''70
    Years''
  
    - ''Breast cancer with BRCA'' -> condition=''breast cancer'', eligibility_keywords=''BRCA''
  
    - ''Recruiting trials for men with prostate cancer'' -> condition=''prostate cancer'',
    sex=''MALE'''
  search_by_sponsor: 'Find all clinical trials sponsored by a specific company or organization.
  
    Functionally equivalent to search_trials(sponsor=...).
  
  
    WHEN TO USE:
  
    - Questions about a company''s pipeline (e.g., ''What is Pfizer working on?'')
  
    - Competitive intelligence (e.g., ''What cancer drugs is Novartis developing?'')
  
    - Tracking pharma company portfolios and development programs
  
    - Finding trials from academic institutions (e.g., ''Mayo Clinic'', ''NIH'')
  
  
    USE search_trials INSTEAD FOR:
  
    - Disease-focused searches where sponsor doesn''t matter
  
    - Finding trials by treatment name rather than sponsor
  
  
    EXAMPLES:
  
    - ''Pfizer Phase 3 trials'' -> sponsor_name=''Pfizer'', phase=[''PHASE3'']
  
    - ''Moderna COVID vaccines'' -> sponsor_name=''Moderna'', condition=''COVID-19''
  
    - ''Active Merck oncology trials'' -> sponsor_name=''Merck'', condition=''cancer'',
    status=[''RECRUITING'']
  
  
    TIPS:
  
    - Partial names work: ''Pfizer'' matches ''Pfizer Inc'', ''Pfizer Pharmaceuticals''
  
    - Set count_total=true to get total number of trials by sponsor
  
    - Combine with phase filter to see early vs late stage pipeline'
  search_investigators: 'Find principal investigators (PIs) and research sites conducting
    trials in a therapeutic area.
  
  
    WHEN TO USE:
  
    - ''Who are the leading researchers in Alzheimer trials?''
  
    - ''Find investigators at Mayo Clinic working on cancer''
  
    - ''Which sites in California are running diabetes trials?''
  
    - Site selection for planning new trials
  
    - Building investigator networks and collaborations
  
  
    USE search_trials FOR:
  
    - Finding trials themselves rather than investigators
  
    - When you need trial details, not investigator info
  
  
    WHAT THIS RETURNS:
  
    - Investigator names and roles (Principal Investigator, Sub-Investigator)
  
    - Institutional affiliations
  
    - Facility/site names
  
    - Geographic locations
  
    - Associated trial NCT IDs and titles
  
  
    TIPS:
  
    - Use condition parameter to focus on a disease area
  
    - Add institution to find investigators at specific hospitals/universities
  
    - Use location for geographic focus (city, state, country)
  
    - Increase page_size to 50-100 for more comprehensive investigator lists
  
    - Use investigator_name for direct name search via advanced query syntax
  
  
    ADVANCED: For direct investigator name search, use the investigator_name parameter
    which searches in OverallOfficialName and ResponsiblePartyInvestigatorFullName fields.'
  search_trials: 'Search ClinicalTrials.gov database for clinical trials. This is the
    PRIMARY tool for finding trials.
  
  
    WHEN TO USE:
  
    - Finding trials for a disease/condition (e.g., ''What trials exist for lung cancer?'')
  
    - Finding trials testing a specific drug/treatment (e.g., ''Find pembrolizumab trials'')
  
    - Finding trials in a geographic area (e.g., ''Clinical trials in Boston'')
  
    - General trial discovery and research questions
  
  
    USE DIFFERENT TOOLS FOR:
  
    - Detailed info on a specific trial by NCT ID -> use get_trial_details
  
    - All trials by a specific company -> use search_by_sponsor (or search_trials with
    sponsor=...)
  
    - Analyzing endpoints/outcomes -> use analyze_endpoints
  
    - Patient eligibility matching -> use search_by_eligibility
  
  
    QUERY SYNTAX (for condition, intervention, sponsor, location):
  
    - Boolean: ''cancer AND immunotherapy'', ''aspirin OR ibuprofen'', ''tumor NOT benign''
  
    - Exact phrase: ''"breast cancer"'' (with quotes)
  
    - Grouping: ''(lung OR breast) AND cancer''
  
    - Synonyms are automatically included (e.g., ''heart attack'' finds ''myocardial
    infarction'')
  
  
    BEST PRACTICES:
  
    - Start with condition parameter for disease-focused searches
  
    - Add status=[''RECRUITING''] to find active trials patients can join
  
    - Use phase filter for specific development stages (PHASE1, PHASE2, PHASE3, PHASE4)
  
    - Set count_total=true to know total matches (useful for: ''How many trials exist
    for X?'')
  
    - Use page_size=50-100 for broader overviews, page_size=10 for quick lookups'
---
# Clinical Trials

An MCP connector for the NIH/NLM ClinicalTrials.gov registry of FDA-regulated clinical
studies worldwide. Supports competitive/pipeline analysis, patient-trial matching,
investigator site selection, and protocol/endpoint research.

## Tools

### search_trials
Find trials by condition, intervention, location, or status. Primary discovery tool.

### get_trial_details
Deep dive into a specific trial's protocol, endpoints, and locations.

### search_by_sponsor
Company/institution pipeline analysis and competitive intelligence.

### search_investigators
Find principal investigators and research sites for a condition/location.

### analyze_endpoints
Systematically compare outcome measures across trials, for protocol/benchmark analysis.

### search_by_eligibility
Match patients to trials based on demographic and clinical criteria.

## Typical workflow

1. `search_trials` to find relevant trials by condition/intervention.
2. `get_trial_details` for each trial needing deeper analysis.
3. `analyze_endpoints` to compare outcome measures across similar trials.
4. `search_investigators` to identify key opinion leaders and active sites.
