---
name: odonata_wing_morphology_skill
description: Identify which Odonata (dragonfly/damselfly) species have reduced pterostigmata or other wing morphology traits based on their flight ecology (glider/migrant vs percher/flier), where the answer is a subset of species indices.
type: sop
version: 1.0.0
require_grad: true
---

# Odonata Wing Morphology Skill

## Goal
Given a list of Odonata species, identify which subset has reduced (or enlarged/absent) wing structures (pterostigmata, wing venation density, wing shape) based on the ecological flight strategy of each taxon.

## When to Activate
- The problem lists Odonata species (dragonflies or damselflies) and asks which have reduced/enlarged/absent pterostigmata or other wing morphological features.
- The problem asks about wing morphology correlated with ecology (migration, gliding, perching, territorial hovering).
- Key vocabulary: pterostigmata, pterostigma, wing spot, Odonata, Libellulidae, Anisoptera, glider, migrant, percher, flier, wing morphology.

## Common Mistakes
- Assuming all dragonflies have well-developed pterostigmata — migratory and gliding species systematically have reduced ones.
- Confusing "reduced pterostigmata" with "absent" — reduction is graded; some gliders have small pterostigmata, not zero.
- Treating the question as purely taxonomic — the deciding factor is **flight ecology**, not family membership alone (though family correlates strongly).
- Ignoring that some species within the same genus differ in migration behavior — resolve to species level when possible.

## Background: Pterostigmata Function and Ecology
Pterostigmata are pigmented cells near the wing tip that act as inertial dampeners, suppressing torsional flutter during active flapping flight. 

- **Perchers / active flappers**: rely on rapid wing-beat cycles for prey capture and territorial defense → need pterostigmata for flutter suppression → **well-developed pterostigmata**.
- **Gliders / migrants**: rely on passive soaring and long-distance wind-assisted flight → wing flutter is less of a concern, and weight reduction is advantageous → **reduced pterostigmata**.

## SOP

### Phase 1 — Classify Each Species by Flight Ecology
For each species in the list:
1. Identify the genus and family.
2. Determine the primary flight strategy from known ecology:
   - **Glider / migrant**: species known for long-distance migration, soaring flight, or open-habitat wandering.
   - **Percher**: species that perch frequently between short foraging flights, defend territories from a fixed perch.
   - **Flier / hoverer**: species that maintain sustained hovering or patrolling flight but are not long-distance migrants.
3. Use the Genus Ecology Reference below when uncertain.

### Phase 2 — Apply Pterostigmata Reduction Rule
- Glider / migrant → **reduced pterostigmata** → include in answer.
- Percher / flier → **well-developed pterostigmata** → exclude from answer.
- For "Varies by species" genera: reason from the ecological signals in *Ecology Clues for Variable-Strategy Genera* — do not blanket-exclude the whole genus, and do not blanket-include.
- Only exclude when ecological evidence is genuinely absent or contradictory.

### Phase 3 — Format Output
- Output indices of species with reduced pterostigmata, comma-separated in ascending order.
- If none qualify, output "none".

## Genus Ecology Reference

| Genus | Family | Flight Strategy | How to assess variable species |
|---|---|---|---|
| Pantala | Libellulidae | Glider / long-distance migrant | — |
| Tramea | Libellulidae | Glider / migrant | — |
| Urothemis | Libellulidae | Glider / open-water migrant | — |
| Tholymis | Libellulidae | Crepuscular glider / migrant | — |
| Macrodiplax | Libellulidae | Coastal/open-water — verify per species | Check if species shows documented transoceanic or mass-movement dispersal; open habitat alone does not qualify |
| Sympetrum | Libellulidae | Varies by species | Include only species with well-documented mass/long-distance migration across broad geographic regions; sedentary or locally-moving species → exclude |
| Libellula | Libellulidae | Varies by species | Include only species with documented large-scale migratory swarms or transoceanic dispersal; territorial perchers in this genus → exclude |
| Orthetrum | Libellulidae | Percher | — |
| Celithemis | Libellulidae | Percher | — |
| Didymops | Macromiidae | Flier / stream patroller (not migrant) | — |
| Anax | Aeshnidae | Strong migrant | — |
| Aeshna | Aeshnidae | Varies by species | Include only species with documented long-distance migratory behavior |

## Ecology Clues for Variable-Strategy Genera
When a genus is marked "Varies by species", reason from the following ecological signals — do **not** rely on genus membership alone:

- **Strong inclusion signals** (reduced pterostigmata likely): species with documented mass migration events, transoceanic or transcontinental dispersal records, open-habitat wandering with no site fidelity, predominant soaring/gliding flight mode.
- **Strong exclusion signals** (well-developed pterostigmata likely): species known primarily as territorial perchers, site-faithful pond/stream residents, short-range foragers with rapid wing-beat bursts.
- When ecological evidence for a species is genuinely absent or contradictory, **exclude** it (do not guess).

## Output Rules
- Comma-separated indices in ascending order.
- "none" if no species qualify.
- No extra text — bare index list only.

## Quick Reference
- [resources/genus_ecology.json](resources/genus_ecology.json) — structured genus-level ecology and pterostigmata data.
