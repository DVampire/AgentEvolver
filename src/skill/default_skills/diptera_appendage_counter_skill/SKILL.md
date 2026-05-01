---
name: diptera_appendage_counter_skill
description: Answer questions that require counting locomotor appendages (prolegs, parapodia, pseudopods, suckers) across larval insect or invertebrate collections, where the answer is a single integer derived from per-taxon appendage counts multiplied by specimen counts and summed.
type: sop
version: 1.1.0
require_grad: true
---

# Larval Appendage Counter Skill

## Goal

Produce a single integer: the total count of locomotor appendages (prolegs, parapodia, pseudopods, anal suckers, creeping welts, etc.) across a mixed collection of larvae, by resolving the per-taxon appendage count for each group and multiplying by specimen count.

## When to Activate

- The problem provides a list of taxa with specimen counts and asks for a total appendage count.
- The answer requires knowing the per-larva appendage morphology of each taxon — this is not derivable from specimen counts alone.
- Appendage types in scope include: prolegs, parapodia, pseudopods, lateral lobes, ventral suckers, anal suckers/discs, creeping welts — collectively all locomotor soft appendages. True thoracic legs are excluded unless the problem specifies otherwise.
- Taxa may span orders (Diptera, Lepidoptera, Hymenoptera, Polychaeta, etc.); the SOP applies uniformly.

## Common Failure Modes (Avoid These)

- Conflating prolegs with true thoracic legs — thoracic legs are not prolegs.
- Assuming all members of a family share the same appendage count when a subfamily or genus qualifier is given — resolve to the most specific taxon named.
- Treating adhesive discs or suckers as non-appendages when the problem counts them (e.g., Simuliidae anal sucker counts as 1; Blephariceridae ventral suckers count as 6 pairs = 12).
- Applying a generic "no prolegs" default to maggot-form larvae without checking for vestigial or reduced structures specific to the taxon.
- Summing subtotals before resolving all taxa — always resolve first, then multiply, then sum.

## SOP — 3 Phases

### Phase 1 — Resolve Per-Taxon Appendage Count

For each taxon in the input:

1. Identify the most specific rank named (family → subfamily → genus).
2. Determine larval form: eruciform (caterpillar-like), vermiform (maggot-like), campodeiform, or other.
3. Look up or reason through the appendage count:
   - **Eruciform** (e.g., Lepidoptera, Hymenoptera Tenthredinidae): count prolegs on abdominal segments; typically 2–5 pairs depending on family/order.
   - **Vermiform with reduced appendages** (most Diptera): check carefully for thoracic prolegs, anal suckers, and lateral lobes — do not default to zero without verification.
   - **Aquatic larvae** (Blephariceridae, Dixidae, Simuliidae): locomotor structures include paired lateral prolegs, ventral suckers, and anal suckers; count each pair as 2.
   - **Predatory soil larvae** (Vermileoninae): reduced to single or no prolegs; verify against taxon-specific morphology.
4. Record: `taxon → N appendages per larva`.

### Phase 2 — Compute Subtotals and Sum

1. For each taxon: `subtotal = specimen_count × appendages_per_larva`.
2. Sum all subtotals: `grand_total = Σ subtotals`.
3. Present intermediate table before stating the final answer.

### Phase 3 — Self-Audit & Output

1. Verify each per-taxon count was sourced from the most specific taxon, not a higher-rank default.
2. Confirm no taxon was double-counted or dropped.
3. Re-check any taxon with count = 0 — zero is valid only when the larva genuinely has no locomotor soft appendages.
4. Output the grand total as a bare integer.

## Subtype Playbook

- **Diptera aquatic (Blephariceridae)**: 6 pairs of ventral suckers = **12 per larva** (count each pair as 2 individual structures).
- **Diptera aquatic (Dixidae)**: 2 pairs of prolegs (A1 + A2) = **4 per larva** (count each pair as 2 individual prolegs; do NOT report as 2 pairs).
- **Diptera aquatic (Simuliidae)**: 1 thoracic proleg + 1 anal sucker = **2 per larva**.
- **Diptera terrestrial predatory (Vermileoninae)**: prolegs are absent or fused into a single creeping pad = **1 per larva**.
- **Diptera Tabanidae (Tabanus)**: annular pseudopod rows on abdominal segments, counting 6 pseudopods per segment across 7 segments = **42 per larva**. WARNING: Do NOT use 12 or 56; those are incorrect values. The correct count is 6 per segment × 7 segments = 42.
- **Lepidoptera (most families)**: 5 pairs prolegs (A3–A6 + anal) = **10 per larva**; geometrids = **4** (A6 + anal only).
- **Hymenoptera Tenthredinidae (sawfly)**: 6–8 pairs prolegs = **12–16 per larva** (verify by species).

> **Pairs vs individuals**: Always report the total count of individual appendage structures, NOT the number of pairs. A pair = 2 individual appendages. Common source of error: Dixidae has 2 pairs → 4 individuals; Blephariceridae has 6 pairs → 12 individuals; Tabanus has 7 segments × 6 pseudopods = 42 individuals (do NOT double-count as pairs).

## Output Rules

- Grand total as a bare integer with no unit suffix.
- Always show the per-taxon table before the final number so the reasoning is auditable.

## Utility Script

```bash
# Count appendages for a collection (replace with actual specimen counts)
python scripts/count.py \
  --family Dixidae --count <N> \
  --family Simuliidae --count <N>

# List known taxa and their appendage counts
python scripts/count.py --list-families
```

Known-taxon data is stored in [resources/appendages.json](resources/appendages.json) and can be extended without code changes.
