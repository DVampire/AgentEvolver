# The coverage gate is a dark-file register, not a percentage

Status: current

## Problem

A recurring defect in this repository is code that is written, shipped, and never called.
A reward computed and written to none of 61 trajectories. A `forget()` and a `claim_due()`
with no call site. A `mutates` flag dropped by three separate registration paths. Search
filters accepted by a tool schema and passed to nobody.

Every one of these was found by reading files one at a time, and every one of them had been
sitting in plain view the whole time as a file the test suite executed no line of. The suite
was green throughout. Nothing in the repository was asking the one question that would have
surfaced them: **which files does the whole suite never reach?**

The obvious way to introduce coverage — measure a percentage and fail below a threshold —
does not answer that question. At the measured baseline of 52.8%, one new unreachable
module moves the number by roughly a tenth of a percent. No threshold can be set tightly
enough to catch that without failing constantly for unrelated reasons.

## Decision

`pytest --cov` measures the suite and applies [tests/coverage_gate.py](../../tests/coverage_gate.py).

The rule is the **set of files at zero coverage**, checked against a register that names
every file allowed to be dark and gives the reason. A file that goes dark without an entry
fails the run. The register is enforced in both directions — see
[the both-ways record](2026-08-15-the-register-is-enforced-both-ways.md).

An overall floor (`FLOOR_PERCENT`) exists alongside it, but it is deliberately secondary. It
catches exactly one thing no per-file rule can see — tests deleted wholesale — and it is a
floor rather than a target.

The gate runs inside `pytest_runtestloop`, not `pytest_terminal_summary`; the reason is
[postmortem 0001](../postmortems/0001-the-coverage-gate-printed-failed-and-exited-zero.md).

Pointed at the repository for the first time, this found `agentevolver/utils/text_compress.py`
— 83 lines, never wired to anything, green in every run since it was added — and four modules
that were reachable in production and reached by no test.

## Alternatives considered

**A global `--cov-fail-under` threshold.** The standard approach, and the one this rejects.
It answers "is there enough testing overall", which is not a question anyone in this
repository was failing to answer. It cannot see a single dead file, and a threshold set high
enough to notice one would fail on every unrelated fluctuation. It is also the version most
likely to be gamed: the cheapest way to raise a percentage is to test whatever is easiest,
which is rarely whatever is riskiest.

**Per-file 100%, as deepseek-harness does.** Their coverage gate requires 100% per file on
every source directory, with the framing that an uncovered line is usually dead code the
gate is correctly flagging for deletion. That is the right end state and the wrong starting
point for an existing codebase at 52.8%: the gate would be red on day one and removed by
week two. Their repository has had it since the beginning, which is the difference.

**Ratchet the percentage — fail if coverage drops below the last recorded value.** Honest,
common, and nearly useless here. It permits any amount of new untested code as long as the
ratio holds, so a large well-tested feature buys headroom for a dead module. It also makes
every legitimate deletion of tested code look like a regression.

**A linter for unreachable code instead of coverage.** Static reachability analysis in
Python cannot see through the registry indirection that this codebase is built on — a tool
registered by name into `tool_manager` and dispatched by string is unreachable to every
static analyzer and perfectly reachable at runtime. Coverage measures what actually ran,
which is the only signal that survives dynamic dispatch.

## Consequences

**What it bought.** The class of defect that used to require reading every file now fails a
run. The first pass found one genuinely dead module and named four real debts, all of which
have since been covered. The register doubles as documentation: every entry states why a
test cannot reach that file, which is a question that otherwise gets asked repeatedly and
answered from memory.

**What it cost.** Measuring adds roughly 30% wall-clock, which is why it is not the default
run and why `pytest` and `pytest --cov` are two different lanes. Anyone adding a genuinely
untestable file now has to write a sentence explaining why — a small tax, paid by the person
best placed to know the answer.

**What it does not do.** It says nothing about whether covered code is *correctly* covered.
A file with one trivial test clears this gate exactly as well as a thoroughly tested one.
Line coverage proves lines ran, never that a feature works as shipped; the mutation tests in
[test_consistency_checks_can_fail.py](../../tests/test_consistency_checks_can_fail.py) exist
because that gap is real.
