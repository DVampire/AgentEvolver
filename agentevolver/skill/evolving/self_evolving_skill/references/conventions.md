# Shared authoring, repair and evaluation conventions

This is the operational reference for the loop in [SKILL.md](../SKILL.md). It applies to
`tool`, `skill`, `agent` (including its prompt), `connector`, `environment`, `memory`,
`workflow` and `plugin`. Type references supply artifact contracts and specific checks.
No family has a separate generation, optimization, evaluation or failure-completion policy.

## Concurrent execution

Call through the component Manager. The existing runtime owns admission, resource claims,
call ownership and cancellation; do not implement another scheduler in a generated entity.
Agent creation stays with kernel dispatch, and Workflow retains its dependency graph.

- Stateless Tool/Plugin implementations may declare `concurrent = True` after checking
  reentrancy. Tools default to exclusive instance access; known filesystem effects also
  claim the workspace. Override `resource_claims(ctx, arguments)` for finer verified scopes.
- Environment defaults to a separate instance per runtime owner. Keep state on that instance;
  `initialize`/`cleanup` run with its binding. Existing multiplexed backends alone use
  `managed_sessions = True` and implement `close_session(owner_id)`.
- Independent Environment evaluations may set `max_concurrency` and override
  `resource_claims(ctx, arguments, operation)` using `ResourceClaim` from
  `agentevolver.runtime.invocation`. Use shared claims for immutable inputs and exclusive
  claims for each trial's outputs. `concurrent = True` removes the default instance claim
  only when the implementation is actually reentrant.
- Mark an action `parallel_safe=True` only when sibling calls can be independent. This
  enables Agent batch admission; resource claims still apply. `read_only` describes effects
  and must remain truthful. Connector/Plugin metadata can declare the same independence;
  Connector metadata `concurrent: true` permits reentrant requests to its remote service.
- Memory defaults to exclusive backend calls. A backend such as `TieredMemory` declares
  `concurrent=True` because it owns namespace transactions and compaction revision checks.
  Its framework projection path is internal: emitting ordinary trace events for each projected event would recurse.
- CPU work needs an owned worker/job backend, not merely `async def`. Register background
  jobs through the framework, retain distinct trial outputs and confirm actual cancellation.

Evaluate two simultaneous owners, same-resource serialization, independent-resource overlap,
failed acquisition, owner exit and serial/concurrent result parity. Do not count `gather`
alone as evidence of isolation or speedup. Skill calls return instructions; their scripts
inherit the execution contract of the Tool/Environment that runs them.

## Author and register

Use the actual paths supplied in the current context. Write candidates under the session's
`{extension_root}/{type}/` staging tree, with supporting artifacts in the locations required
by the type. Do not overwrite package source, a shared installed component or a candidate
still being consumed. Temporary checks and fixtures belong in the authorized workspace.

Skill source locators remain in native descriptions after compaction. Invoke the skill again
for resource paths rather than guessing a category directory or an old reference filename.
Use paths returned by actions for their outputs. Resolve a reference's relative links from
that reference's directory, not the workspace. Check an unfamiliar shell utility with
`command -v`; if absent, use an available equivalent instead of repeating the failed command.

Inspect before writing: read the target's source, related files, current version and
`enable_evolving`. Missing means generate; frozen means create an alternative under a new
name when needed; evolvable means optimize the existing name. Preserve the prior good version
and baseline evidence before installing a candidate. Read exact source context, keep
unrelated edits intact and use the mounted editing tools for the smallest correct change.

Preserve input/output compatibility. If a correction requires a contract change, document
the change and update authorized consumers or introduce a compatible alternative. Keep
manifests, schemas, documentation and implementation consistent. Declare real side effects;
an HTTP GET that writes an artifact is not a read-only operation. Do not broaden permissions
or falsify effects to make a denied operation pass.

Perform the type's pre-registration checks: syntax, manifest parsing, dependency imports,
default/intended construction or workflow compilation as applicable. Check the real success
and failure path where feasible. A compile pass does not prove initialization or execution.

Register with `adoption_tool` using `action="register"`, `module`, `name` and the absolute
`artifact_path` specified by the type reference. Use the existing session authorization and
runtime permission policy; this skill adds no separate approval ceremony. Do not edit
framework registries/imports or restart the agent to install a candidate. Component-local
imports such as `__init__.py` are valid when required by its loader.

Writing bytes does not create a version. On success, preserve the reply's candidate version,
active version, artifact path and rollout status. Managers may assign versions; never guess
a bump or re-register unchanged code to repair a report. An active candidate is live but
provisional. Under shadow/canary rollout, ordinary calls may use the baseline; use supported
candidate execution and verify attribution. If it is unavailable, the candidate remains
unverified. Registration does not migrate a running instance or grant consumer capabilities.

## Diagnose and recover in the same agent loop

Read the actual failed call, schema and source before choosing a fix. Record the cause and
the next changed action; the same failing call without new evidence is not a repair attempt.

| Observation | Next action |
|---|---|
| Bad arguments or a wrong path | Inspect the exposed schema and actual filesystem; correct the call. Change the component only if its interface/docs are defective. |
| Registration rejects syntax, imports, construction or a manifest | Repair the staged artifact or its declared dependencies, repeat the relevant check and register again. A rejected attempt has no version to adopt. |
| Discovery or invocation fails in authored code | Inspect the underlying error and implementation, preserve the failed version's evidence, repair in staging and register/evaluate the changed version. Local MCP server code follows this path too. |
| Permission/effect mismatch | Check actual effects and allowed paths. Correct inaccurate declarations or target paths within existing authorization; do not relabel writes as reads. A real missing grant remains a prerequisite. |
| Runtime reports success but the result is wrong or incomplete | Treat the behavior as a failed case; fix the implementation and ensure real execution failures reach the caller through that type's failure contract. A report describing an error is not a successful required operation. |
| Evaluation reveals a regression or no claimed benefit | Record the failed comparison, restore the prior good version or unload a new candidate, then revise using the evidence if the opportunity still warrants it. |
| Evidence/report submission is rejected | Read the validator's reason, correct the report or collect missing evidence for the same version. Do not edit or re-register working code solely to fix call IDs. |
| External access, credentials or a service is unavailable | Distinguish that source's constraint from general infeasibility. Probe permitted alternatives with a bounded effort and continue useful independent work. Preserve a real unresolved prerequisite if none works. |

Record a failed registered candidate before rollback/unload when possible; cleanup should
not be abandoned because receipt submission itself failed. A decision does not execute
rollback/unload: call the operation and inspect the resulting active state. Do not consume
a known failed candidate as though it were kept. Preserve old evidence, source lineage and
the original requirement; each repaired version needs its own checks.

Retry the original failed native operation after repair, plus a different regression case.
Missing code or a fixable local server is implementation work, not a requirement that the
user supply an external MCP endpoint. If the defect belongs to a frozen framework component,
do not overwrite it; investigate an authorized alternative and document the precise blocker
if none is feasible. Stop identical retries or an unproductive experiment, not all useful
task work. Never call `done_tool` merely because one component failed.

## Evaluate behavior, with fixed candidate source

Freeze the candidate source and comparison inputs while evaluating a version. Evaluation
is immutable evidence, not a read-only mode for the entire agent. Exercise authorized writes
in isolated files, state or service sandboxes, verify reset/cleanup and keep unrelated work
intact. A failing check returns to authoring a new candidate; do not patch the evaluated
source in place or relabel its failed evidence as passing.

Exercise the registered component through the framework and verify the actual version.
Supporting scripts may construct fixtures, calculate reference outputs or check artifacts.
Their success alone does not prove that the native Tool, Connector, Environment, Agent,
Memory, Workflow or Plugin consumer works. A Skill must be invoked and its method executed;
loading instructions is not outcome evidence.

Run an expected rejection separately from actions that must execute: the serial executor
stops a batch at its first failed call. Record the expected reason before the check, retain
the failed receipt, verify that state did not advance, and then execute the valid recovery.
Do not call a missing-file crash a passing negative test when the intended phase/contract
rejection was never reached. Keep such defects distinct from successful rejection tests.

Use a representative baseline/candidate comparison and an independent reuse or regression
case for a small change. Expand for affected operations, state transitions, permissions,
recovery and downstream consumers when the change is broader. Keep inputs, model, capabilities
and budgets comparable. For nondeterministic claims, use proportionate repeated observations
and report uncertainty. Preserve earlier successful cases during optimization.

The current agent can run deterministic comparisons and judge concrete outputs. When the
claim depends on fresh model behavior, use an available authorized consumer with equivalent
contexts and no leaked candidate answer. Do not require MetaAgent or `general_agent` to exist,
or pretend a continuing conversation has forgotten a skill. If necessary execution is
unavailable, record the limitation as inconclusive rather than passing from source review.

Evaluate the original required successful operation on valid inputs, not only graceful
rejection of missing inputs. Mark an expected negative case as passing only if its observed
behavior matches the expectation; it cannot substitute for positive coverage. Disclose
tested scope and untested limits. A verified narrower improvement leaves the broader gap
open until it is actually resolved.

Use correctness, robustness, interface compliance, maintainability and performance as review
lenses. Numerical scores are optional; they do not replace acceptance checks. Report measured
latency, calls/tokens or other relevant cost when claiming savings, separating observations
from estimates. Source review can identify risk but cannot measure performance or prove use.

## Record a decision for the evaluated version

Call `adoption_tool action="record_decision"` with `report`, `decision` (`keep`, `rollback`
or `unload`) and `evidence` explaining the need, comparison, benefit and remaining limits.
The tool's schema is authoritative. The report has this shape (replace placeholders with
observed values):

```json
{
  "module": "tool",
  "name": "example_tool",
  "version": "<exact returned candidate version>",
  "verdict": "pass",
  "baseline": "<prior version or existing method and its observed result>",
  "cases": [
    {
      "case_id": "comparison-1",
      "kind": "comparison",
      "expected": "<required operation and acceptance criterion>",
      "observed": "<actual baseline and candidate results>",
      "passed": true,
      "evidence_ids": ["<actual tool_call_id>"]
    },
    {
      "case_id": "reuse-1",
      "kind": "reuse",
      "expected": "<criterion on a different input or operation>",
      "observed": "<actual independent result>",
      "passed": true,
      "evidence_ids": ["<actual different tool_call_id>"]
    }
  ]
}
```

- `module` names one of the eight families; an agent's prompt is supporting Agent content.
- The version must exist in the archive. `keep` requires a passing verdict for the exact
  active version; never credit a baseline call to a shadow candidate.
- Each case needs a unique `case_id`, `expected`, `observed`, boolean `passed` and nonempty
  `evidence_ids`. Failed and inconclusive evaluations use real observed calls too.
- An evidence ID is a `tool_call_id` copied from this run, not a tool name, file path,
  invented label or worker's private call ID. For delegated work, cite the observed call
  that returned the evidence and retain its underlying report. Record decisions promptly
  while the relevant calls are available.
- `pass` requires executed passing cases and the claimed benefit. Record failure or missing
  evidence honestly; no aggregate score can cancel a required failing case.

For non-kept provisional changes, perform the recorded rollback/unload and verify the result.
For a kept version, invoke it on subsequent real work and verify the consumer's output. If
the consumer is instance-bound, use a supported fresh instance or handoff; a registry entry
is not instance migration. Return to the loop when actual use exposes a defect.

## Additional receipts when the task explicitly requires evolution

For `evolution.require_verified_improvement`, include `capability_gap` with `user_need`,
`required_operation`, `limitation`, `acceptance_criterion`, `observation_evidence_ids` and
`baseline_evidence_ids`. Observation and baseline calls must precede registration. Include
`kind="comparison"` and an independent `kind="reuse"` or `"regression"` case.

After keep, use the adopted version synchronously on real subsequent work, then call
`adoption_tool action="record_use"`. Its report contains `module`, `name`, exact `version`,
`consumer_call_id`, `evidence_ids` and `outcome`. Cite successful calls at or after that
consumer call, including the consumer itself where appropriate. For a Skill, cite its load
and subsequent operations that executed the method. Earlier baseline/evaluation evidence
belongs in the decision. An instance-only change needs instrumented consumer execution.
Do not submit `record_use` for ordinary tasks without this declared requirement.

Track each explicitly required family separately. Lifecycle receipts establish component
evidence; they do not override the task's substantive acceptance criteria. Keep detailed
artifacts in the work record, with concise status and paths in the plan index when available.
