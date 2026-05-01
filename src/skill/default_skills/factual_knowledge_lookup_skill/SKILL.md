---
name: factual_knowledge_lookup_skill
description: Resolve factual knowledge lookup questions across any domain (humanities, chemistry, biology, physics, pop culture) where the answer is a unique proper noun, integer, or classification and multiple constraints must be conjointly satisfied against an authoritative source.
type: sop
version: 2.0.0
require_grad: true
---

# Factual Knowledge Lookup Skill

## Goal
Conjoin every hard constraint in the problem statement and narrow down to a unique proper noun, integer, or classification by anchoring to the authoritative source that defines the answer — not general consensus or memory.

## When to Activate
- The problem asks "who / what / which / how many" — a unique-attribution or enumeration lookup.
- The answer is a proper noun (person / work / place / molecule name / color name) or a specific integer.
- The problem stacks multiple constraints that must all be satisfied simultaneously.
- The answer depends on a specific classification standard or naming convention (e.g. IUPAC, CAS, a specific textbook's taxonomy, a brand's official catalog).

## Common Failure Modes (Avoid These)
- Drawing a conclusion from a single cue without conjoining all stated constraints.
- Using general knowledge consensus instead of anchoring to the specific authoritative source the question implies.
- For chemistry/biology: applying a more conservative or more liberal classification than the source uses (e.g. excluding allotropes that a specific textbook counts, or including impure forms it excludes).
- Giving a near-miss answer on obscure topics (adjacent dynasty, adjacent author, adjacent molecule, adjacent color).
- For multilingual / archaic text, failing to translate the key phrase before searching.

## SOP — 4 Phases

### Phase 1 — Constraint Decomposition & Source Identification
1. Split each hard constraint in the problem into its own line (domain, classification standard, naming convention, era, author, brand, etc.).
2. **Identify the authoritative source** the question is likely anchored to:
   - Humanities: original text, specific encyclopedia, official wiki.
   - Chemistry: IUPAC recommendations, CAS registry, RSC (rsc.org), ACS (acs.org), or a specific textbook (e.g. Cotton & Wilkinson, March's Advanced Organic Chemistry, Greenwood & Earnshaw). **Do NOT use Wikipedia as the primary source for chemistry classification questions** — Wikipedia often reflects the most conservative mainstream view and omits edge-case allotropes, polymorphs, or isomers that specialized sources include.
   - Biology: a specific taxonomy database (NCBI, ITIS), a standard reference, or an official nomenclature.
   - Pop culture / trivia: official product catalog, manufacturer's website, game wiki.
3. For multilingual text, translate the key phrase first before searching.
4. For chemistry classification questions (e.g. "how many allotropes / colors / isomers"): **search multiple sources** (RSC, ACS, specialist review articles) and report the count from the broadest credible classification, since Wikipedia and general sources tend to undercount by excluding edge cases (e.g. scarlet phosphorus, fibrous red phosphorus variants). If sources disagree, report the highest count from a peer-reviewed source.

### Phase 2 — Candidate Pool × Conjunctive Intersection
1. Use the strongest constraint to collapse the pool to within ~10 candidates.
2. Conjoin each remaining constraint and drop candidates that fail any of them.
3. Do not conclude on a single keyword — require at least two independent constraints to converge.
4. For numeric enumeration questions: explicitly list every candidate item, then apply the classification filter from Phase 1. Count surviving items — do not estimate from memory.
5. For chemistry classification questions: search RSC or ACS for review articles or reference works on the topic (e.g. `site:rsc.org "allotropes of phosphorus"`), not Wikipedia. Enumerate every item found across all sources, then apply the pure/impure filter. Wikipedia is a last resort only.

### Phase 3 — Source Cross-Verification
1. Surviving candidates must match at the original-source level, not merely "looks similar".
2. For numeric questions, enumerate every item and count — do not estimate.
3. When the authoritative source is a specific textbook or catalog, cite the exact edition, page, or entry.
4. For chemistry: if different sources give different counts or names, report the value from the most specific/authoritative source the question implies. If ambiguous, report the range and flag the discrepancy.

### Phase 4 — Output Normalization
1. For personal names, preserve language-appropriate spelling (accents, particles).
2. For molecule / compound names, use the name format the question asks for (IUPAC, common, CAS).
3. Integer answers are bare arabic numerals.
4. Color names: use the exact official name from the relevant catalog or standard.

## Subtype Playbook
- **Literary / philosophical attribution**: resolve to work → passage → speaker, a three-level index.
- **Named molecule trivia** (e.g. "how many carbons in mercedesbenzene", "how many fluorines in perfluoronanocar"): look up the molecule's structure in a chemical database (PubChem, ChemSpider) or the naming paper; do not derive from memory.
- **Allotrope / isomer / polymorph count**: explicitly enumerate from a specific reference — counts vary by source. Search for the classification used in the original exam context.
- **Brand / product color names** (e.g. Crayola crayon containing a specific dye): consult the official product catalog or manufacturer's ingredient list, not a general pigment database.
- **Extinct-species-named molecules**: search by the molecule name pattern (e.g. "-ane" suffix for hydrocarbons) + the extinct taxon name in chemical databases.
- **Pop culture / game trivia**: consult the open-source repo or official wiki — never estimate.
- **Cryptography + pop culture**: reconstruct the substitution table strictly from the problem's stated key, then resolve the reference.

## Output Rules
- Person name → full name (with diacritics).
- Molecule / compound → name in the format requested.
- Integer → arabic numerals only.
- Color name → exact official name.
- **Technical term / metric name**: give only the canonical term in the relevant field's standard. Do not enumerate synonyms unless asked.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
