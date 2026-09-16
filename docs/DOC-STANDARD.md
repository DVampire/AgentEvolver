# Documentation standard

Two questions this file answers: **where does a fact live**, and **what does bad
documentation look like** so it can be recognised in review.

## One home per fact

Every fact belongs to exactly one tier. Everywhere else, link to it.

The cost of ignoring this is not duplication — it is *divergence*. Two copies of a rule stay
in sync until the first time only one of them is updated, and from then on the repository
contains two answers with no way to tell which is current.

| Tier | Its job | What does **not** belong there |
|---|---|---|
| [README.md](../README.md) / [README_zh.md](../README_zh.md) | What this project is and how to start using it | Contributor procedure, design rationale, per-module detail |
| Module `README.md` | That module's contract: what it owns, its shape, its extension points | Restating a docstring, another module's concerns |
| Docstrings | The local contract: behaviour, failure modes, ownership, timing, non-obvious orientation | Reasoning transcripts, test walkthroughs, code restatement |
| [tests/README.md](../tests/README.md) | How a test in this repo is written, and why | What any individual test asserts |
| `docs/<topic>.md` | A reference for one subject: current behaviour, lookup-shaped | Teaching sequences that belong in a tutorial |

Placement, in one line: **contract, and the standing orders that go with it → the docstring
or module README that owns the thing.**

There is deliberately no repo-wide standing-orders page. `PROJECT.md` was one, and every
rule on it was a second copy of something with an owner — so it drifted from all of them at
once, silently, because nothing imports a document.

## Writing rules

**Document current state, not change history.** "Previously", "now", "no longer", "used to",
"was renamed" — none of these belong in durable prose. Name the live mechanism. Change
stories go in commits.

**Say why the wrong answer is tempting.** This is the highest-value sentence in most
documents and the one most often missing. Restating what the code does is nearly free and
nearly worthless; explaining the reading that a careful person would otherwise adopt is what
survives a refactor.

**Reserve emphasis for the clause that changes behaviour.** Bold everywhere is bold nowhere.

**Cross-reference with relative Markdown links**, never bare filenames or prose references,
so [test_doc_links.py](../tests/test_doc_links.py) can check them.

## The slop checklist

Run this over any document. Each line is a pattern, not a rule of taste — every one of them
has produced a real defect somewhere.

1. **The same rule stated in more than one home.** Grep a distinctive phrase. Keep one home,
   link the rest.
2. **Narrated history**: "previously", "now", "no longer", "used to", commit or PR
   references. State the current fact.
3. **Implementation-status annotations**: "implemented!", "TODO: future". Status rots faster
   than anything else in a document; the code and the manifests carry it.
4. **Hand-restated inventories** — lists of tests, modules, or tools that a generator or the
   directory listing already owns. They are wrong within a month.
5. **Reasoning transcripts**: step-by-step narration of how something was implemented, proofs
   of obvious branches, test walkthroughs. Keep the resulting contract; delete the path used
   to derive it.
6. **Rationale repeated beside every sibling** instead of stated once at the thing that owns
   it.
7. **Paragraph walls**: one paragraph carrying several rules plus parenthetical asides. Split
   it, or demote the detail to its home.
8. **Emphasis inflation**: bold, caps, and "critically" everywhere.
9. **Plan language in a shipped document**: "should", "we will", migration steps, acceptance
   checklists. A shipped document describes what *is*.

## What is already enforced

These are gates, not conventions — they fail a run:

| Gate | What it checks |
|---|---|
| [test_doc_links.py](../tests/test_doc_links.py) | Every file a document points at exists |
| [test_module_readmes.py](../tests/test_module_readmes.py) | Every module has a README with machine-readable frontmatter |
| [test_translation_pairing.py](../tests/test_translation_pairing.py) | A translation and its source stay the same document |
| [test_prompt_layout.py](../tests/test_prompt_layout.py) | Templates and the stylesheet that renders them agree |

Everything above this table is convention, checked in review.
