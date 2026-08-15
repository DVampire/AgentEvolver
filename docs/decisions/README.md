# Decisions

A decision record answers one question: **why is it like this, and what did we give up?**

Code says what happens. Tests say what must keep happening. Neither says why the third
option lost, and that is the part someone re-litigates six months later — usually by
re-deriving the same trade-off, sometimes by reversing it without knowing there was one.

## When to write one

One test: **would someone reasonably ask "why was this done this way?" in six months?**

That is a lower bar than "was this hard" and a much higher bar than "did I change
something". A record is worth writing when the change closed off an option that still
looks attractive from the outside — a threshold that could have been a different number,
a mechanism that could have been simpler, a rule that will feel arbitrary to whoever hits
it next.

Not worth writing: anything the code already explains, anything a test already pins, and
anything whose alternatives were never real.

## The shape

```markdown
# <Title: the decision, as a claim>

Status: current

## Problem
<The motivation, written so it stands without the solution. If a reader has to
already know the answer to understand the question, this section is wrong.>

## Decision
<What is true now, in the present tense.>

## Alternatives considered
<Each real alternative and why it lost. Bold-led paragraph per alternative.>

## Consequences
<What the trade-off cost AND what it bought. Both halves.>
```

Two rules carry the whole format. Everything else is convention.

### 1. `## Alternatives considered` is mandatory

A decision recorded without what it beat invites the exact re-litigation these files exist
to prevent. If a decision genuinely had no alternative, it did not need a record.

Alternatives are **recorded, never invented**. Writing a plausible-sounding rejected option
to fill the section is worse than leaving the file unwritten: it fabricates a deliberation
that never happened, and the next reader will treat it as history.

### 2. `## Decision` is present tense

These files describe **what is**, not what someone planned. No "will", no "should", no
migration steps, no acceptance checklists — that language belongs in an issue or a PR
description, and once the work ships it becomes a trap: a reader cannot tell an intention
from a fact.

When the code later moves a file, renames something, or changes a default, update the
**facts** in the record in the same change. Do not update the **decision** — if the
decision itself changed, that is a new record.

## Superseding

A record is never edited into a different decision. Write a new one, link the two, and move
the old file to `superseded/` with its `Status:` line changed to
`Status: superseded by <relative link>`.

Keeping the old file matters: the new decision is only legible next to the one it replaced.

## Naming

`yyyy-mm-dd-topic-in-words.md`, where the date is when the topic was **first raised**, not
when it shipped. Cross-reference other records with relative Markdown links so
[test_doc_links.py](../../tests/test_doc_links.py) can check them.

## Index

- [The coverage gate is a dark-file register, not a percentage](2026-08-15-coverage-gate-is-a-dark-file-register.md)
- [The dark-file register is enforced in both directions](2026-08-15-the-register-is-enforced-both-ways.md)
- [The measured lane excludes the slow tests](2026-08-15-the-measured-lane-excludes-slow-tests.md)
