# The measured lane excludes the slow tests

Status: current

## Problem

The coverage gate compares a measured run against a register of files allowed to be dark
([record](2026-08-15-coverage-gate-is-a-dark-file-register.md)). That comparison is only
meaningful if the register and the run agree on **which tests ran**. Coverage is a property
of a selection, not of the code: run half the suite and half the repository is at zero.

Two selections were plausible. The default one — `-m "not integration"`, which includes the
`slow` mutation tests — and the same minus `slow`. Calibrating the register against one and
running the other produces contradictions in both directions: a file the slow tests reach
looks dark in the narrow lane, and registering it to satisfy that lane makes the wide lane
fail with "now covered, delete the entry".

The `slow` tests also cost far more than they contribute here. Each one parks a backup of a
real source file, mutates it, and re-runs pytest in a fresh interpreter to prove a gate goes
red. Subprocess coverage is deliberately not collected — measuring it would credit lines to
a source tree that no longer matches the one on disk — so those runs add minutes of
wall-clock and nothing at all to the numbers.

## Decision

`pytest --cov` is the whole command. When coverage is active and the marker expression is
still the default, `pytest_configure` in [tests/conftest.py](../../tests/conftest.py)
rewrites it to `GATED_MARKEXPR` — `"not integration and not slow"`.

There is exactly one measured selection, it is reached by the shortest command anyone would
type, and the gate refuses to judge any other. `_selection_is_whole_suite()` verifies the
marker expression, the absence of `-k`, and that no narrower path or node id was named;
`pytest --cov tests/test_job.py` prints that it stood down and why, rather than reporting
every other file in the repository as dead code.

Paths are compared after resolution, so `tests`, `tests/`, and an absolute path are one
selection. A string comparison silently disabled the gate for two of the three spellings.

## Alternatives considered

**Make the wide selection canonical and pay the minutes.** Correct if slow tests
contributed coverage. They do not: their parent-side code is file mutation and subprocess
launching, and everything they actually exercise runs in a child whose coverage is not
collected. Paying several times the wall-clock for zero additional signal would make the
lane something people skip, and a gate that is skipped is not a gate.

**Document the exact command instead of rewriting the marker.** `pytest --cov -m "not
integration and not slow"` in the README, and trust everyone to type it. This is a rule that
lives only in prose, which makes it a rule that holds until someone is in a hurry. Worse, the
failure is quiet: the wrong selection produces a plausible-looking result with the wrong
register comparison, not an error.

**Accept several marker expressions as valid gated lanes.** Tempting, since slow tests
contribute nothing — both selections should give the same numbers. But "should" is doing all
the work: the moment they diverge, the register can satisfy one lane or the other and not
both, and the contradiction shows up as a confusing gate failure rather than as the
selection problem it is. One lane means one answer.

**A dedicated `--coverage-gate` flag or a separate tox environment.** More explicit, and one
more thing to know. `--cov` is already the flag that means "measure this run"; giving the
measured run one definition is cheaper than inventing a second name for it.

## Consequences

**What it bought.** One command, one selection, one set of numbers the register is
calibrated against. The lane runs in about 80 seconds, which is short enough that it gets
run.

**What it cost.** `pytest --cov` no longer runs the same tests as `pytest`. That is
surprising the first time and is why the rewrite is loud in the config comment and stated in
[tests/README.md](../../tests/README.md). Anyone who genuinely wants slow tests measured has
to pass `-m` explicitly, at which point the gate stands down and says so.

**What still holds.** The slow tests are not skipped in general — they run in the ordinary
`pytest` lane, which is where they belong. This decision changes what the *measured* lane
covers, not what CI verifies.
