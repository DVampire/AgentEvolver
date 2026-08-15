# Postmortems

A bug reached somewhere it should not have, and the interesting part is **why the process
let it through** — not the one-line fix.

A postmortem is not a [decision record](../decisions/README.md). That one is forward-looking:
a deliberate choice and the options it beat. This one is backward-looking: what broke, the
mechanism, why every safety net missed it, and the guardrail added so the same *class* of
bug fails loudly next time.

## When to write one

All three, or it does not belong here:

| | |
|---|---|
| **Subtle** | The mechanism is non-obvious. A careful engineer would re-derive it the hard way. |
| **Systemic** | It escaped because of a gap in tests, tooling, or conventions — not a one-off typo. |
| **Costly to rediscover** | It cost real debugging time, and it would cost that again. |

Most bugs fail the second test. A typo caught by review is not a postmortem; a typo that
three separate checks looked straight past is.

Keeping these rare is what makes them worth reading. A directory of forty incident reports
is a directory nobody opens.

## The shape

Open with an **executive summary**: one short paragraph a busy reader absorbs in thirty
seconds — what broke, the root cause in plain terms, why it escaped, and the durable lesson.
Everything after that is for the reader who wants the mechanism.

```markdown
# <number>. <What broke, as a sentence>

## Executive summary
## What happened
## The mechanism
## Why every check missed it     ← the section the postmortem exists for
## Guardrails
```

`## Why every check missed it` is the point. Listing the checks that *should* have caught it
and explaining, one by one, why they did not, is what turns an incident into a change in how
the repository is verified.

## Index

- [0001 — The coverage gate printed FAILED and exited 0](0001-the-coverage-gate-printed-failed-and-exited-zero.md)
