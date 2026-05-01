---
name: novel_spec_simulation_skill
description: Execute a fabricated computing architecture or rule specification (e.g. Bagua / Wuxing / Yin-Yang instruction sets) exactly as written — suppress prior binary/hex intuition and simulate every step against the provided spec.
type: sop
version: 1.0.0
require_grad: true
---

# Novel Spec Simulation Skill

## Goal
Execute the fabricated spec exactly as written and emit a bit-perfect numeric or string answer.

## When to Activate
- The problem defines a fabricated ISA / storage model / encoding / numeric base from scratch (Bagua / Wuxing / a custom RISC).
- The answer depends on strict spec execution; it commonly takes the form "float_precision:integer" or a specific number.
- The problem contains many custom symbols, non-standard numeric bases, and unusual instruction names.

## Common Failure Modes (Avoid These)
- Replacing the spec's octal / decimal quirks with familiar binary / hex intuition.
- Missing "corner" rules (special registers, exception handling, carry rule).
- Simulating without a state table and letting mental arithmetic errors accumulate.
- Forgetting to count memory usage in the spec's native units (e.g. trits, digits, chars) — never count in bytes unless the spec defines bytes.

## SOP — 3 Phases (phase count tailored to this skill's natural workflow)

### Phase 1 — Transcribe the Full Spec
1. Transcribe the spec into a structured spec-card: {data unit, numeric base, register table, instruction set, memory model, I/O, exceptions}.
2. Flag every item that conflicts with x86 / IEEE-754 intuition — those are the pitfalls to watch.
3. Confirm the answer shape (bare number / "a:b" / long string).

### Phase 2 — Build a Python Simulator (NOT mental math)
1. Implement the ISA in Python strictly; every register / memory cell / float precision follows the spec.
2. If the problem embeds a physics formula (Schwarzschild time dilation, Rayleigh-Jeans, projectile motion), implement that formula in Python using the spec's data types and rounding rules — do not substitute approximations or mental arithmetic.
3. If the problem provides a worked example, reproduce its output as a self-test; if the self-test fails, return to Phase 1.
4. For custom floating-point precision, implement the mantissa / exponent field widths explicitly — never fall back to IEEE-754.
5. Memory usage: count in the spec's native units (trits for Bagua, D for Wuxing, etc.). Determine the minimum variable set needed by the program, then sum up their sizes.

### Phase 3 — Run, Self-Audit, Format
1. Run the simulator on the target program; print a register / memory snapshot at each step if needed.
2. If your intuition says the answer "should be X", double-check the spec.
3. Emit the answer in the exact format of the problem's example ("0.993:8", "747"); no units, no prefixes.

## Subtype Playbook
- **Bagua architecture**: base-8, 8 channels, ISA keyed on trigrams; all arithmetic runs in octal. Memory unit = trit (3 bits); int = 8 trits; frac = numerator + denominator + exponent fields per spec.
- **Wuxing architecture**: decimal-based; frac type = 3 chars × 2D = 6D per variable. For physics-embedded problems (projectile, blackbody), implement the formula in Python with spec rounding (e.g. round to nearest 0.5, truncate to integer thousands).
- **Answer format "value:memory"**: value is the computed numeric result (rounded per spec precision); memory is the total native-unit size of all variables used. Compute both independently, then join with colon.
- **Fabricated RISC**: decode strictly from the opcode table; never assume x86 / ARM semantics.

## Output Rules
- Output matches the problem's example exactly ("0.993:8" keeps both decimal places and separator).
- Never replace the spec with intuition from a familiar numeric base.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
