"""The rule the coverage lane enforces: no module may be unreachable by accident.

Not a test module — `conftest.py` calls `violations()` at the end of a `--cov` run, and
`test_coverage_gate.py` proves the function actually goes red.

The percentage is the boring half. The useful half is the set of files the whole suite
never executes even one line of, because that set contains two very different things
wearing the same clothes: code nothing tests, and code nothing *calls*. Several defects
in this repo were the second kind and were found by hand, one file at a time — a reward
that was computed and never written to any of 61 trajectories, a `forget()` and a
`claim_due()` with no call site anywhere, a `mutates` flag dropped by three separate
registration paths. Every one of them was sitting in this list the entire time.

So the register below is a debt ledger, not an excuse list, and it is enforced in both
directions. A file that goes dark and is *not* registered fails the run — that is the new
defect. A registered file that starts being covered *also* fails the run, demanding its
entry be deleted. Without that second direction the register only grows, and a list that
only grows stops being read.
"""

from __future__ import annotations

from typing import Dict, List, Mapping

#: Overall statement coverage may not fall below this. Deliberately a floor and not a
#: target: it exists so a large deletion of tests cannot pass unnoticed, and it is raised
#: by editing this number after the lane reports a higher one. It does not decide whether
#: any particular line is worth testing — the register below does that, file by file.
FLOOR_PERCENT = 52.0

#: Files the suite executes no line of, each with the reason it is allowed to stay dark.
#: Shrinking this is the point. Adding to it is a decision that needs a sentence, and the
#: sentence has to say why a test cannot reach the file — not that writing one is a chore.
NEVER_EXECUTED: Dict[str, str] = {
    # --- Not implemented yet -------------------------------------------------
    # Both say so in their own docstrings. There is no behaviour here to test; when one
    # grows a body it also leaves this list, and the gate will insist on that.

    # --- Process entry point -------------------------------------------------
    # Runs only under `python -m agentevolver.gateway`. Importing it *is* running it, so
    # a test that covered this line would start a gateway as a side effect of collection.
    "agentevolver/gateway/__main__.py": "module entry point — import executes it",

    # The four entries that stood here — gateway/transport.py, extension/journal.py,
    # extension/smoke_gate.py, hook/promotion.py — were reachable in production and
    # reached by no test. They are covered now (tests/test_gateway_transport.py,
    # test_evolution_journal.py, test_smoke_gate.py, test_promotion.py) and the gate
    # would fail this file if their entries were left behind.
}


def violations(
    covered_lines: Mapping[str, int],
    total_percent: float,
    registered: Mapping[str, str] = NEVER_EXECUTED,
    floor: float = FLOOR_PERCENT,
) -> List[str]:
    """Every way the measured run breaks the rule, as sentences a reader can act on.

    Args:
        covered_lines: Repo-relative source path → lines the run executed. Files with no
            statements at all must be left out by the caller; an empty ``__init__.py``
            reports zero covered lines while having nothing to cover, and failing a run
            over one would teach everyone to ignore this gate.
        total_percent: Overall statement coverage the run reported.
        registered: The debt ledger. Injectable so the gate's own tests can drive it.
        floor: Minimum overall coverage.

    Returns:
        One string per violation, empty when the run is clean.
    """
    dark = {path for path, lines in covered_lines.items() if lines == 0}
    problems: List[str] = []

    for path in sorted(dark - set(registered)):
        problems.append(
            f"{path}: no test executes a single line of this file. Either it is "
            f"unreachable and should be deleted, or it needs a test. If neither, add it "
            f"to NEVER_EXECUTED in tests/coverage_gate.py with the reason."
        )

    # The other direction. A registered file that is now covered has paid off its debt,
    # and leaving the entry behind would keep the file exempt from the check it just
    # started passing — which is how a register quietly stops meaning anything.
    for path in sorted(set(registered) & set(covered_lines) - dark):
        problems.append(
            f"{path}: now covered, but still listed in NEVER_EXECUTED. Delete its entry "
            f"({registered[path]})."
        )

    if total_percent < floor:
        problems.append(
            f"overall coverage {total_percent:.1f}% is below the floor {floor:.1f}%. "
            f"If tests were removed on purpose, lower FLOOR_PERCENT in the same commit "
            f"and say why."
        )

    return problems
