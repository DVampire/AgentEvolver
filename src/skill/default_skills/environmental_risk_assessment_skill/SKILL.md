---
name: environmental_risk_assessment_skill
description: Calculate environmental exposure metrics including soil contamination concentration, soil-water partitioning using Koc/Kd, PFAS bioaccumulation factors (BAF/BCF), plant uptake via TSCF, estimated daily intake (EDI), hazard quotient (HQ), mixture toxicity interactions (synergism/antagonism), 1D advection-dispersion of contaminants through groundwater or fractures, POP accumulation in fish, and steady-state contaminant concentration in freshwater bodies.
type: sop
version: 1.1.0
require_grad: true
---

# Environmental Risk Assessment Skill

## Goal
Compute environmental risk quantities (soil concentration, porewater concentration, plant concentration, estimated daily intake, hazard quotient, BAF/BCF) from a contamination scenario with given soil parameters, contaminant mass, and exposure conditions.

## When to Activate
- The problem describes a contamination scenario (fire foam, spill, spray) with soil area, depth, organic carbon content, bulk density, and volumetric water content, and asks for a concentration or hazard metric.
- The question involves Koc, Kd, TSCF (transpiration stream concentration factor), plant uptake factor, or BAF/BCF.
- The question asks for a Hazard Quotient (HQ) or whether a mixture has synergistic or antagonistic toxicity.
- Contaminants include PFAS compounds (PFOS, PFOA, PFHxS) or other persistent organic pollutants.

## Common Failure Modes (Avoid These)
- **Using log Koc instead of Koc**: Koc is 10^(log Koc). If a source gives log Koc = 2.31, then Koc = 204 L/kg, not 2.31 L/kg.
- **Forgetting unit conversion for foam concentration**: If foam concentration is given in μg/L, convert to μg total = volume_L × concentration_(μg/L).
- **Mixing up C_soil units**: After computing total mass and soil volume, divide correctly to get consistent units (μg/kg = mg/ton; μg/L for porewater).
- **Wrong mass-balance formula**: Total contaminant mass M = (C_w × V_water) + (C_s × M_soil), where C_s = Kd × C_w. Solve for C_w first.
- **Using PUF and TSCF simultaneously**: PUF (plant uptake factor) and TSCF are two separate pathway models. Use TSCF for aqueous uptake: C_plant = TSCF × C_porewater. Use PUF for solid-phase: C_plant = PUF × C_soil. Choose the pathway based on which variables the problem provides.
- **Forgetting to multiply TSCF by uptake factor when both are given**: If the problem gives both plant_uptake_factor and TSCF, use: C_plant = C_porewater × TSCF × plant_uptake_factor.
- **Mixture toxicity classification error**: Do NOT default to "additive" without checking for known mechanistic interactions. Classify as follows:
  - **Synergistic**: when contaminants share a mechanistic pathway or one enhances bioavailability/toxicity of the other. Check for known synergistic pairs in the literature before defaulting to additive. For example, certain pesticide–PFAS combinations have documented synergistic effects on aquatic organisms.
  - **Additive**: combined effect equals the sum of individual effects; applies when no mechanistic interaction is documented and HQ total is derived by simple concentration addition (CA model).
  - **Antagonistic**: one contaminant reduces the effect of the other.
  - **Key rule**: Toxic Unit (TU) sum alone does not determine synergism/antagonism — the biological mechanism must be considered. If the question provides specific chemical identities and an organism target (e.g., algae), consult the chemical-specific interaction data for that combination rather than defaulting to the CA model.
- **Wrong inflow term in steady-state water concentration**: For a freshwater body with contaminated inflow, the steady-state water concentration is C_ss = (C_in × Q_in) / (Q_out + Q_in × (1 + Kd × f_oc)), NOT simply C_in × Q_in / Q_out. The denominator must include the sorption sink term.

## SOP — 5 Phases

### Phase 1 — Compute Total Contaminant Mass
1. Total mass M₀ = foam_volume_L × concentration_(μg/L) [or equivalent for other source terms].
2. Check units: convert to a consistent base (μg recommended for PFAS problems).

### Phase 2 — Compute Soil Parameters
1. Soil volume V_soil = area_m² × depth_m (result in m³; convert to L using 1 m³ = 1000 L if needed).
2. Soil mass M_soil = V_soil_m³ × bulk_density_kg/m³.
3. Porewater volume V_w = V_soil × θ_w (θ_w = volumetric water content, dimensionless L/L).
4. Fraction of organic carbon f_oc = OC% / 100.
5. Kd = Koc × f_oc (L/kg).

### Phase 3 — Partition Contaminant into Soil and Porewater
Using mass balance:
- M₀ = C_w × V_w + C_s × M_soil, where C_s = Kd × C_w
- M₀ = C_w × (V_w + Kd × M_soil)
- C_w = M₀ / (V_w + Kd × M_soil) [units: μg/L if M₀ in μg, V_w in L, M_soil in kg]
- C_s = Kd × C_w [units: μg/kg]

### Phase 4 — Compute Plant Concentration and Daily Intake
1. **Plant concentration via TSCF**: C_plant = C_w × TSCF × plant_uptake_factor [μg/L × dimensionless × dimensionless = μg/g (wet weight)]
   - If plant_uptake_factor is not given, use C_plant = C_w × TSCF.
   - Convert units as needed (μg/L porewater → μg/g food using density ~1 g/mL for water-based concentrations).
2. **Daily intake per food item**: DI = C_plant × daily_mass_g × bioavailability_factor / 1000 [μg/day if C_plant in μg/g]
3. **Total daily dose (EDI)**: EDI = Σ DI / body_weight_kg [μg/kg/day]

### Phase 5 — Compute Hazard Quotient and Mixture Toxicity
1. HQ = EDI / Reference_Dose (RfD) [both in μg/kg/day; HQ is dimensionless]
2. If HQ > 1: risk exceeds acceptable level.
3. For dynamic accumulation in a water body: use the time-dependent equation C(t) = C_ss × (1 − e^{−(k+r)t}) + C₀ × e^{−(k+r)t}, where C_ss = C_in × Q_in / (Q_out + Q_in × (1 + Kd × f_oc)), k = ln(2)/half_life, r = Q_out/V. Convert half-life to consistent time units before computing k.
4. For fish/organism accumulation via POP model: daily accumulation = C(t) × Q_gills × AF_gills + C_food × IR_food × AF_food − k_elim × C_fish × M_fish. This is a net rate (ng/day); interpret as the daily change in fish body burden.
5. For mixtures: compute HQ_total = Σ HQ_i; classify interaction based on mechanism (see Common Failure Modes for synergism rules):
   - **Synergistic**: mechanistic enhancement documented for the specific chemical combination and target organism.
   - **Additive**: no mechanistic interaction; CA model applies.
   - **Antagonistic**: one contaminant reduces the other's effective toxicity.

## Subtype Playbook
- **PFAS in soil from fire foam (single contaminant)**: Use Phase 1–5 sequentially. Key step: correctly look up Koc for the specific PFAS compound; log Koc for PFOS ≈ 2.57 (Koc ≈ 370 L/kg), for PFOA ≈ 2.06 (Koc ≈ 115 L/kg).
- **Multiple PFAS with separate BAF/BCF given**: C_food = C_water × BAF or C_food = C_soil × BCF. Compute EDI and HQ independently per compound, then sum.
- **Soil Koc calculation (reverse)**: If soil and water concentrations are given, Kd = C_s/C_w; Koc = Kd/f_oc.
- **Mixture toxicity + HQ**: Compute individual HQs. Classify interaction based on documented mechanisms for the specific chemical combination and target organism — do not default to additive without checking for known synergistic pairs.
- **Dynamic POP accumulation in fish (time-based)**: Step 1: compute C(t) in water using the transient equation with half-life, flush rate, and inflow. Step 2: compute daily net accumulation in fish. Note that half-lives given in years must be converted to days (1 year ≈ 365.25 days) before computing the decay constant k = ln(2)/half_life_days.
- **1D advection-dispersion with sorption retardation**: Use C(x,t) = M_total/√(4πDt) × exp(−(x−vt)²/4Dt) × exp(−kt) × 1/(1+Kd×C_gw), where C_gw is the solid-to-water mass ratio = bulk_density / (volumetric water content) in kg/L. Compute C_gw from the problem's density and moisture data — do not treat it as a free parameter.

## Output Rules
- Show each intermediate calculation step (M₀, V_soil, M_soil, V_w, Kd, C_w, C_plant, EDI, HQ) before the final answer.
- Report HQ rounded to the precision matching the input data (typically 2-4 significant figures).
- For mixture problems, report total HQ and the interaction classification on the same line.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
