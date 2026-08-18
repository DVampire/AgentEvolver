# Evaluating a workflow

## What it is

A workflow is one HTML file, `{extension_root}/workflow/{name}.html`, containing a
`<workflow>` element inside a complete `<html>` document with a DOCTYPE.

**The contract**: it compiles, and its declared inputs and outputs stay stable — canvas nodes and
other workflows already target them by name.

## Evaluating a Workflow

Evaluation is read-only:

1. Call `inspect_tool` (capability_type="workflow"); confirm name, version, active status, source, and contract.
2. Check schema safety, bounded termination, path coverage, capability existence, retry and
   verification policy, input/output clarity, and applicability precision.
3. Run a representative case when safe. Compare with the prior active version or manual
   orchestration when available.
4. Score outcome quality from 0.0–1.0 and report success, real run id, case id, elapsed time, token cost,
   and concrete notes.
5. Call `evolution_tool` with `action=record_workflow_evaluation`. Recommend keep,
   optimize, rollback, or unload from concrete evidence.

A version is provisionally healthy after at least 3 evaluations, success rate at least
0.8, and average quality at least 0.7. This summary guides retention; it does not create a
second publication state. Representative coverage and the absence of safety or termination
defects remain mandatory.
