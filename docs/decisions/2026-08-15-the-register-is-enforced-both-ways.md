# The dark-file register is enforced in both directions

Status: current

## Problem

The coverage gate ([record](2026-08-15-coverage-gate-is-a-dark-file-register.md)) allows a
file to have zero coverage when it is named in a register with a reason. That much is
forced: some files genuinely cannot be reached by a test — a module entry point that runs on
import, a scaffold with no implementation yet.

An allowlist checked in one direction only has a predictable failure mode. It grows. Every
entry is individually defensible at the moment it is added, nothing ever removes one, and
within a year the list is long enough that nobody reads it. At that point it has stopped
being a record of decisions and become a place to put things.

There is a sharper failure underneath the slow one. Suppose a registered file gets tests
written for it. If its entry stays, that file is now **permanently exempt from the check it
just started passing**. Delete its tests later and it goes dark again — and the stale entry
waves it straight through. The exemption outlives the reason for it, silently.

## Decision

`violations()` in [tests/coverage_gate.py](../../tests/coverage_gate.py) reports two kinds
of failure, not one:

1. A file at zero coverage that is **not** in `NEVER_EXECUTED` — the new-dead-code case.
2. A file in `NEVER_EXECUTED` that **is** now covered — the stale-entry case. The message
   names the file and echoes back its recorded reason, so the reader can judge whether it
   ever applied.

The second rule is what makes the register a ledger rather than an allowlist: it can only
shrink. Paying off a debt is not optional bookkeeping — the run stays red until the entry
is deleted.

Four entries were removed this way. `gateway/transport.py`, `extension/journal.py`,
`extension/smoke_gate.py`, and `hook/promotion.py` were all reachable in production and
reached by no test; the gate named them, they were covered, and the gate then required their
entries to go.

## Alternatives considered

**One direction only, plus a periodic manual review.** The default. It relies on someone
choosing to audit a list that, by construction, nobody has a reason to open. Every mechanism
in this repository that depends on remembering to look has eventually stopped being looked
at; the register is enforced by the same run that produces the data, so there is nothing to
remember.

**An expiry date per entry.** Each registration gets a date after which the run fails. This
does force review, but on a clock unrelated to the work: an entry expires while its file is
untouched and the person on call that week either extends the date without context or writes
a test they have no reason to be writing. Coverage changing is the event worth reacting to,
and that is exactly what the both-ways check watches.

**Deleting the register and requiring a test for every file.** Cleanest rule, and wrong. It
would demand a test for `agentevolver/gateway/__main__.py`, whose only line runs on import
— covering it means starting a gateway as a side effect of collection. Genuinely
untestable files exist, and a rule that pretends otherwise gets bypassed rather than
followed.

**Warn instead of fail on a stale entry.** Warnings in a passing run are read once and then
never again. If a stale entry is worth detecting, it is worth stopping for; if it is not
worth stopping for, the check should not exist.

## Consequences

**What it bought.** The register is small and every entry is live. Nine entries at the time
of writing: two scaffolds that say so in their own docstrings, one module entry point whose
import *is* its execution, and — after the burndown — nothing else.

**What it cost.** Writing tests for a registered file now requires a second edit, in a
different file, to delete its entry. The gate names the file and quotes the reason back, so
the edit is mechanical, but it is a real step and it will occasionally surprise someone.

**A subtlety worth knowing.** Both directions depend on the measured selection being the
whole suite. Run a subset and every unrelated file is at zero; run a failing suite and the
zeroes mean "unreached because broken". The gate detects both cases and stands down with a
message saying which — see [the measured-lane record](2026-08-15-the-measured-lane-excludes-slow-tests.md).
