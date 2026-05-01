---
name: dna_translation_skill
description: Translate a DNA sequence to amino acids by identifying the correct reading frame and strand, including 6-frame translation, ORF identification, and template vs coding strand disambiguation.
type: sop
version: 1.1.0
require_grad: true
---

# DNA Translation Skill

## Goal
Produce the correct amino acid sequence (single-letter or three-letter code as specified) from a given DNA sequence by identifying the correct reading strand and reading frame, starting from the first valid start codon (ATG) in the correct frame.

## When to Activate
- The problem provides a DNA sequence and asks for the translated amino acid sequence.
- The question asks for "the first protein translated" or "the amino acid sequence following transcription."
- The problem specifies forward/reverse, 5'→3'/3'→5', coding/template, or sense/antisense strand.

## Common Failure Modes (Avoid These)
- **CRITICAL — Declaring answer after forward-strand ATG gives ≤ 2 amino acids**: If translating the forward strand from its first ATG produces only 1–2 amino acids (e.g., "MG"), this is NOT the answer. A ≤ 2 amino acid result is a mandatory stop signal — you MUST immediately compute the reverse complement and scan all 6 frames before reporting any result. Never accept a ≤ 2 amino acid forward-strand ORF as the final answer when the question asks for "the first protein".
- **Translating only the forward strand when the answer requires the reverse complement**: "Forward sequence 5'→3'" is the coding strand, but the gene of interest may be on the complementary strand. If no ORF (open reading frame starting with ATG that produces a meaningful protein) exists on the forward strand, check the reverse complement.
- **Stopping too early at a short ORF**: The first ATG in the given sequence direction may produce only 1-2 amino acids. If the question implies a longer protein (e.g., "a protein"), scan all 6 frames.
- **Treating the end of the sequence as a stop codon**: Absence of a stop codon just means the ORF extends beyond the given sequence. Continue until an explicit stop codon (TAA, TAG, TGA in the template, or UAA, UAG, UGA in mRNA) is found or the sequence ends.
- **Forgetting to complement when taking the reverse complement**: The complement of A→T, T→A, G→C, C→G; THEN reverse the entire string.
- **Single-letter vs three-letter code**: Output exactly the format requested. Single-letter is the default unless three-letter is specified.

## SOP — 4 Phases

### Phase 1 — Clarify Strand and ALWAYS Check Both Strands
1. Note the stated strand direction (5'→3' or 3'→5') and type (coding/sense vs template/antisense).
2. If "forward sequence 5'→3'" is given: the sequence IS the coding strand. The template strand is its reverse complement.
3. If the question asks about "transcription of this region": transcription reads the template strand and produces mRNA identical to the coding strand (T→U). The gene expressed may be encoded on either strand.
4. **MANDATORY**: Always scan ALL 6 reading frames (3 forward + 3 reverse complement). Do NOT stop after finding the first short ORF on the forward strand — a 1-2 amino acid result is a strong signal that the actual gene is on the reverse complement strand.

### Phase 2 — Generate All 6 Reading Frames
1. Forward frames: scan the given sequence in frames starting at position 0, 1, and 2. For each frame, find all ATG positions and translate until stop or end.
2. Compute the reverse complement:
   - Step 1: Replace each base A↔T, G↔C
   - Step 2: Reverse the entire string
   - Example: 5'-AGTTGC-3' → complement TCAACG → reverse GCAACT = 5'-GCAACT-3'
3. Reverse complement frames: scan the reverse complement sequence in frames starting at position 0, 1, and 2.
4. For each of the 6 frames, record: (frame label, ATG position, translated sequence, length).

**Decision rule**: If the forward strand gives an ORF ≤ 2 amino acids and the reverse complement gives an ORF ≥ 5 amino acids, the reverse complement ORF is the answer. **A ≤ 2 amino acid forward-strand result is a HARD STOP — do not report it as the answer without first completing the full 6-frame scan.**

### Phase 3 — Translate from the Correct ORF
1. Select the ORF that is: (a) the longest among all 6 frames, or (b) the only meaningful ORF (≥ 3 amino acids).
2. Translate each successive codon using the standard genetic code until a stop codon (TAA, TAG, TGA) or end of sequence.
3. Record the full amino acid sequence starting from M (ATG).

### Phase 4 — Verify and Report
1. Confirm the selected ORF produces a longer/more meaningful sequence than any alternative frame.
2. State explicitly which strand (forward or reverse complement) and which frame offset (0, 1, or 2) produced the answer.
3. Report in the requested format (single-letter uppercase by default).

## Subtype Playbook
- **Short forward-strand ORF only 1-2 amino acids**: Always check the reverse complement. A meaningful protein is typically 5+ amino acids.
- **"Following transcription"**: mRNA has same sequence as coding strand (T→U). If the given sequence is already the coding strand, translate directly (T→U is implicit).
- **"Template strand" given**: The template strand runs 3'→5'. To get mRNA: take the complement of the template strand (which equals the coding strand) and read 5'→3'.

## Output Rules
- State which strand and reading frame was used.
- Show the DNA→mRNA→protein conversion explicitly.
- Report the amino acid sequence in the exact format requested (single-letter uppercase is default).

## Do NOT Activate For
- Isotope tracing / metabolic flux analysis (e.g. ¹³C-labeled substrate tracking through glycolysis or the TCA cycle) — use a metabolic pathway tracing approach instead, tracking which specific carbon positions (C1, C2, … Cn) pass through each reaction step and emerge as labeled CO₂ or labeled products.
- Protein structure prediction or RNA secondary structure.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
