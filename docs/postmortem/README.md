# Postmortems

A postmortem records a failure that **escaped** — it was not caught by a test, a review, or a
measurement, and the interesting part is why nothing caught it.

## Postmortem or note?

An [Agent Note](../../.agents/notes/README.md) records a decision that was made: what was
chosen, what it rules out, what would make it wrong. It looks forward.

A postmortem looks backward at something that went wrong. It is worth writing when all three
of these hold:

- **The mechanism is not obvious.** A careful engineer reading the fix would not reconstruct
  why the bug existed. A typo does not qualify; three defects each hiding the next does.
- **The escape is systemic.** The reason nothing caught it is a gap in how this repo tests or
  measures, not a one-off oversight. "The fixture was built from an assumption instead of from
  a real log" is systemic; "nobody ran the test" is not.
- **Rediscovering it would be expensive.** It cost real debugging time, and the same class of
  bug would cost it again.

Most bugs fail all three and get a commit message. If a postmortem produces a decision, the
decision gets its own note and the postmortem links to it.

## Format

`NNNN-slug.md`, numbered in order, opening with `# Postmortem NNNN: <title>` and then:

```markdown
## Summary
## What happened
## Why nothing caught it
## What changed
```

`## Summary` is one paragraph a reader can absorb in thirty seconds: what broke, the root
cause in plain terms, why it escaped. `## Why nothing caught it` is the section the postmortem
exists for — a write-up that only explains the bug is a commit message with headings.

| # | Title |
|---|---|
| [0001](0001-derive-context-shipped-broken-and-was-measured-as-working.md) | `derive_context` shipped broken, and the broken run was measured as a success |
