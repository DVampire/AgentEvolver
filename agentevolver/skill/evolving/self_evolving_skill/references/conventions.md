# Conventions

What holds across all eight component types, per operation. A type's own file — `<type>/<type>.md`
— says what that component *is* and what its contract requires; this says how the work is done
whatever the type turns out to be.

## Writing a new one

**Where things go.** Write to `{extension_root}/{target_type}/`, and nowhere else. That is this
session's staging tree; a component is promoted to the shared extension root only after
validation and explicit approval. Mounted framework source and the shared library are writable;
keep generated candidates in staging so their evaluation and adoption remain recorded. Temporary verification scripts
go in `{workspace_root}` — never the project root.

**How registration happens.** You never edit an `__init__.py`, never touch a registry, and never
restart anything. Call `adoption_tool` with `action="register"`, the component's `module` and
`name`, and its **absolute path** as `artifact_path`; that promotes it out of staging and
registers it. Nothing you wrote is a version until this call succeeds — the file is bytes on
disk, `inspect_tool` still reports it unregistered, and a decision recorded against it is
refused. A refusal names what to fix; fix the artifact and register again.

Registration used to ride on `done_tool.reasoning`, because installing was the last act of a
worker whose whole run was the evolution. You do this work yourself now, mid-run, and the
install is a call you make when the artifact is ready.

**Verify before you finish.** Every type has a check, named in its file: Python compiles
(`python -m py_compile /abs/path.py && echo "syntax OK"`), a manifest directory has its manifest
where the loader expects it, a workflow compiles. Then exercise what you built at least once — a
component that has never been run is a guess.

**Name it once.** Check the name is not already taken (`inspect_tool`) before writing.
A second component under an existing name is refused at registration if the first is frozen, and
silently replaces it if not.

## Changing an existing one

**Check the gate first.** `inspect_tool` with the target's type gives its source path
and `enable_evolving`. **Frozen means stop**: a frozen component cannot be optimized — the write is
refused at registration. Report that it is frozen and say a new component in `extension/` is the
way, rather than trying and failing.

**Read before you write.** Read the current source; never assume its contents. If related files
are listed, read those too — they may hold dependencies or tests your change affects.

**The smallest correct change.** Do the thing the task asks and nothing else; do not refactor
around it. Use the tools actually mounted for this run: `apply_patch_tool` for targeted
patches or `bash_tool` for scoped edits when available and authorized. Inspect exact source
context, keep unrelated changes intact, and overwrite only when a rewrite is genuinely
necessary. Do not request an unmounted file tool just to follow an editing example.

**Preserve the contract.** What the contract is, the type's file says — a tool's `__call__`
signature and `Response`, a skill's frontmatter, a plugin's tool ids, a workflow's declared
inputs. Change it only when the task explicitly asks, because everything already pointing at it
breaks silently.

**Write to the staging tree.** The improved version goes under `{extension_root}/{target_type}/`,
never over a file in `{package_root}`.

**Verify, then register.** Run the type's check after every edit, then any available test or a
quick functional call. Then call `adoption_tool` with `action="register"` and the changed
component's **absolute path** — that is how the new version gets registered.

## Judging one

### Evaluating changes nothing

You never edit the thing you are judging — a grader with write access can resolve a bad grade by
editing what it graded, so this run is read-only. The one exception is recording your own verdict
through `adoption_tool`.

**Read the exact candidate version once**, then record the version and source path in the
existing work record. Reuse source reads and scores only for that version in this evaluation;
if the target changes, inspect and evaluate the new version rather than recycling old evidence.

**Then exercise it.** The evidence is what you observed, not what you expect from reading. How to
exercise each type is in its file; in general, call a tool directly, invoke a skill and apply
its method to a representative case, dispatch an agent on a small task, or make an authorized
read-only call against a connector or environment. Reading instructions alone does not prove
that a skill improves outcomes. If necessary execution or isolation is unavailable, report
inconclusive rather than bypassing permissions.

**Match verification to the change.** For a small method change, compare one representative
baseline/candidate case and one independent reuse or regression case. The baseline may already
work: test the claimed improvement, not just whether a defect disappears. Expand coverage for
broader, stateful, permission-sensitive or externally mutating changes to the affected operations,
isolation, recovery and relevant regressions. Never omit required safety checks. State the tested
scope and untested limits; sampled success does not establish that every operation works.

**No standalone scripts.** Do not write an eval script for `bash_tool` or `code_interpreter_tool`
— those run in a fresh process where the target is not registered, so the run proves nothing.
Exercise the component through the framework.

### Recording the verdict

Scoring is not the last step: an evaluation nobody recorded cannot be adopted. Pass the
judgment to `adoption_tool` as `report`, alongside `decision` and `evidence`. It is validated
on arrival, so it has to be the real shape:

```
report = {
  "module":  one of tool | skill | agent | connector | environment | memory | workflow | plugin,
  "name":    the component's registered name,
  "version": the exact version you evaluated — `adoption_tool` action `register` replies with
             it, and `inspect_tool` reports it; never invent it, and never assume registering
             again bumped it,
  "verdict": "pass" | "fail" | "inconclusive",
  "baseline": what you compared against, in words,
  "cases": [
    {"case_id": "unique-within-this-report",
     "expected": "what should happen",
     "observed": "what did happen",
     "passed": true,
     "evidence_ids": ["toolu_… — the tool_call_id of a call you made, copied verbatim"]},
    ...
  ],
}
```

Four rules the validator enforces, each of which rejects the whole record rather than
degrading it:

- `case_id` must be unique inside the report.
- `evidence_ids` must be non-empty for every case, and each one must be the `tool_call_id`
  of a call this run actually made — copy it from the conversation. A tool's *name*, or a
  label you compose (`case-1`, `eval:ACC1`), is checked against the calls on record and
  rejected. A case with no evidence is an assertion.
- **`verdict: "pass"` requires at least one case and every case passing.** A pass with no
  executed cases is refused — this is where a verdict argued from reading the source dies.
- The `version` must already be archived, and `decision: "keep"` additionally requires that
  it is the manifest's *active* version. Evaluate the candidate you actually installed.

### The five dimensions

Each scored 0–20, total 100.

**Correctness (20)** — does it produce the expected result for valid input? Test the scoped
operations with known inputs; for a full component review, cover every supported operation.
20 = all scoped cases pass; −4 per failing case. Disclose coverage with the score.

**Robustness (20)** — does invalid input fail gracefully rather than crash? Test missing
arguments, wrong types, out-of-range values. Expected: a failure *returned*, not an unhandled
exception — a crash surfaces to the caller as an action error. 20 = all handled; −5 per unhandled
exception.

**Interface compliance (20)** — does it honour the contract its type declares? The type's file
names that contract. Deduct per missing or malformed element.

**Quality (20)** — is the source readable and free of obvious defects? Judge from reading alone:
clear naming, no dead code, appropriate error handling. Syntax is implicitly valid — it would not
be registered otherwise. Deduct per issue found.

**Performance (20)** — does it respond acceptably? Judge from the source and your own calls; flag
blocking I/O or heavy work in a hot path. A network call with no timeout is a finding regardless
of how fast it was when you tried it. Precise timing is not required.

Report per-dimension scores with the evidence behind each, and concrete suggestions an optimize
run could act on.
