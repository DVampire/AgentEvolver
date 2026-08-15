---
name: tests
description: "How a test file in this repo is written: what its docstrings must say, how its imports and sections are ordered, and why a test that only describes itself is not enough."
version: 1.0.0
type: module
category: testing
requirements: []
metadata: {}
---
# Tests

A test here has two readers. One is the runner, which only needs the assertion. The other
is whoever arrives in a year holding a red result and no idea whether the invariant broke
or the test went stale. Everything below exists for the second reader.

## The shape

```python
"""<One line: what this file establishes, stated as a claim.>

<Two to five sentences: the failure this prevents. Concrete — what actually went
wrong, or what would go wrong and how it would look. Not a restatement of the
assertions.>
"""

import asyncio                      # stdlib
from pathlib import Path

import pytest                       # third-party

from agentevolver.job import job_manager        # first-party


# --------------------------------------------------------------------------- #
# <Section name — only in files with more than about six tests>
# --------------------------------------------------------------------------- #
def test_a_killed_job_is_not_a_failed_one():
    """<One line: the property, as a sentence.>

    <Optional, and the valuable part: why this case is worth its own test — the
    reading it rules out, the bug it caught, the cost of getting it wrong.>
    """
    ...
```

## Rules

1. **Every file opens with a module docstring** whose first line names the subject and
   whose body says what breaks without the file. One line is not enough: a single line
   almost always restates the filename.

2. **Test names read as sentences.** `test_a_killed_job_is_not_a_failed_one`, not
   `test_kill_status`. The name is the claim; a reader who only skims names should still
   learn what holds.

3. **A docstring is required wherever the name cannot carry the reason.** Skip it only
   when the name is the whole story. Prefer explaining *why the wrong answer is tempting*
   over restating the assertion — the assertion is already right there.

4. **Import order: stdlib, third-party, first-party**, one blank line between groups.

5. **Sections** with the `# ---` banner once a file passes roughly six tests, named for
   the behaviour they group, not for the code under test.

6. **Comments inside a test explain the setup, not the syntax.** A magic number, an
   unobvious fixture, a value chosen to sit just past a threshold — those earn a comment.
   `# call the function` does not.

## Why the "why" is mandatory

A test that says only what it asserts cannot be maintained. When it fails, the reader has
to reconstruct from scratch whether the behaviour was deliberate — and the cheapest way
out of that is to change the assertion until it passes. Several defects in this repo
survived precisely that way: a fixture built its events in the opposite order from a real
log, the code was written to match the fixture, and the two agreed with each other for
months while neither agreed with reality.

Record the failure, not the behaviour. "Six serializers each had to learn `ToolMessage`
and none had" survives a refactor; "checks that ToolMessage serializes" does not.

## Two kinds of test live here

Most check that a unit does what it says. A smaller set checks that facts **duplicated
across files still agree** — every provider handling every message type, every template
agreeing with the stylesheet that renders it. They are ordinary pytest files, not a
separate mechanism; what makes them different is that they *discover* their subjects from
the code rather than listing them, so something added later is covered by existing.

`test_consistency_checks_can_fail.py` guards that set the only way that means anything:
it reintroduces each real defect and requires the check to go red. A check that cannot
fail reports the invariant as held, which is worse than no check and silent forever.

## The coverage lane

```sh
pytest                # the ordinary run — no measurement, no gate
pytest --cov          # the gated run: measures, then applies tests/coverage_gate.py
```

The second one adds roughly 30% wall-clock, which is why it is not the default. What it
buys is the one question no individual test can ask: **which files did the entire suite
never execute a line of?** That set holds two things that look identical from the outside
— code nothing tests, and code nothing *calls* — and only reading them tells which. The
first file it was ever pointed at, `utils/text_compress.py`, turned out to be the second
kind: 83 lines, written, never wired to anything, green in every run for months.

Every dark file must be named in `NEVER_EXECUTED` in [coverage_gate.py](coverage_gate.py)
with the reason a test cannot reach it. The list is enforced in both directions — a file
that starts being covered has to *leave* it — so it can only shrink. Treat an addition as
a small debt taken on, not a checkbox.

The gate stands down on a subset (`-k`, an explicit path) and on a run with failures, and
says so: in both cases the zeroes are artifacts of what did not run, not findings about
the code. `test_coverage_gate.py` drives the rule directly, so the gate's own logic is
proven without paying for a measured run.
