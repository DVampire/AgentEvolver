# 0001. The coverage gate printed FAILED and exited 0

## Executive summary

A new coverage gate was wired to `pytest_terminal_summary`. It detected its target case
correctly and printed a red `coverage gate: FAILED` banner naming the offending file — and
the process still exited 0. pytest fixes the exit code from `session.testsfailed` the moment
`pytest_runtestloop` returns, so anything hooked after that point can only decorate. The
gate survived its own unit tests, a full green suite, and an end-to-end probe that confirmed
the red banner; only checking `$?` exposed it. The durable lesson is that **a check is
verified by its exit code, not by its output** — every other signal was present and correct
while the gate was, in the only way that matters to CI, silently disabled.

## What happened

The gate ([decision record](../decisions/2026-08-15-coverage-gate-is-a-dark-file-register.md))
was written, unit-tested, and run against the whole suite. All green.

To prove it could fail, a probe module with a few unreachable statements was written into
the package and the measured lane was run again. The output was exactly right:

```
coverage gate: FAILED (1)
  - agentevolver/_gate_probe.py: no test executes a single line of this file. ...
```

The command that produced it also printed the exit code:

```
exit=0  (期望非 0)
```

The gate had found the defect, described it accurately, and reported success.

## The mechanism

pytest decides the run's exit status inside `_pytest.main._main`:

```python
def _main(config, session):
    config.hook.pytest_collection(session=session)
    config.hook.pytest_runtestloop(session=session)
    if session.testsfailed:
        return ExitCode.TESTS_FAILED
```

`session.testsfailed` is read the instant `pytest_runtestloop` returns. `pytest_sessionfinish`
runs after that, and `pytest_terminal_summary` runs later still — from inside the terminal
reporter's own `pytest_sessionfinish` wrapper. Incrementing `testsfailed` at that point
mutates a counter nobody will read again.

The fix was to move the gate into a `pytest_runtestloop` wrapper whose post-`yield` half runs
after pytest-cov has stopped and saved coverage — which it does, because conftest is
registered after the pytest-cov plugin and therefore wraps outside it.

pytest-cov carries the same comment above the same hook, for the same reason:

```python
# we need to wrap pytest_runtestloop. by the time pytest_sessionfinish
# runs, it's too late to set testsfailed
```

That comment was found *after* the bug, by going to read how `--cov-fail-under` manages to
fail a run. It was two files away the entire time.

## Why every check missed it

This is the part worth keeping.

**The unit tests passed, correctly.** `test_coverage_gate.py` drives `violations()` directly
with constructed inputs. Every one of those tests was right, and none of them could have
caught this: the defect was not in the rule, it was in where the rule was attached. A pure
function tested purely says nothing about its wiring.

**The full suite passed, correctly.** With no violation present, a gate that cannot fail and
a gate that has nothing to fail on produce identical output. Green told us nothing.

**The end-to-end probe passed — and this is the real failure.** A probe module was written,
the lane was run, and the red banner appeared. That was treated as proof. It was proof of
*detection*, and detection was never in doubt; what was in doubt, and what went unchecked,
was *enforcement*. The verification stopped at the most visible signal.

**The exit code was printed on screen and not read.** `exit=0 (期望非 0)` was in the output
of the command that ran the probe. The expectation was even written next to it. It was
skimmed past on the way to the banner, because the banner was the thing being looked for.

The common thread is not carelessness about one check. It is that **every layer of
verification examined output, and the contract being verified was about exit status**. Four
checks, one blind spot, shared by all of them.

## Guardrails

**A test asserts the wiring, not just the rule.**
`test_the_gate_is_wired_to_the_test_loop_not_the_terminal_summary` in
[tests/test_coverage_gate.py](../../tests/test_coverage_gate.py) requires
`pytest_runtestloop` to exist in conftest and `pytest_terminal_summary` to be absent. It is
cheap, it names the exact failure in its docstring, and it goes red if anyone moves the gate
back.

**The lane is verified in both directions, by exit code.** Clean run: exit 0, gate green.
Probe module present: exit 1, gate red. Both were re-run after the fix and both were checked
with `$?`, not by reading the banner.

**The rule, stated where the next person will hit it.** The hook carries a docstring
explaining that a gate attached later prints its banner and still exits 0, that this was
written the late way first, and that only `$?` caught it. The comment exists so the next
person choosing a hook does not have to rediscover pytest's ordering.

**The general lesson, for checks not yet written.** When adding any gate, the acceptance
criterion is *the run fails* — demonstrated by a non-zero exit — never *the message
appears*. A gate that only prints is a gate CI walks straight past, and it looks exactly
like a working one from the terminal.
