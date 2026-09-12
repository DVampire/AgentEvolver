# Workflow

This reference defines the type's artifact contract and specific checks. Follow the
[shared lifecycle](../../SKILL.md) and [conventions](../conventions.md) for inspection,
staging, registration, failure repair, evaluation, adoption and actual consumer use.

## What it is

A workflow is one HTML file, `{extension_root}/workflow/{name}.html`, containing a
`<workflow>` element inside a complete `<html>` document with a DOCTYPE.

**The contract**: it compiles, and its declared inputs and outputs stay stable — canvas nodes and
other workflows already target them by name.

One methodology for the complete Workflow lifecycle. A Workflow is a reusable orchestration
program, not an Agent subtype and not a substitute for a Skill's domain instructions.

## Writing a new one

### Creating a Workflow

- Inspect active Workflows first and avoid semantic duplicates.
- Generalize concrete task details into typed `<inputs>`.
- Write one complete HTML document to `{extension_root}/workflow/{name}.html`.
- Use a `<workflow>` element with `name`, semantic `version`, `schema-version="1.1.0"`,
  `status="active"`, a precise `description`, and `enable-evolving="true"`.
- Include `<applicability>` tags and prose explaining when to use and when not to use it.
- Use only supported callable nodes: `agent`, `tool`, `skill`, `connector`, `environment`,
  `workflow`. Connector and Environment nodes require an `action`.
- Use only bounded control nodes: `parallel`, `map`, `branch`, `loop`, `verify`, `reduce`,
  `checkpoint`. Every loop requires `max-rounds`; all fan-out needs a concurrency bound.
- Values use restricted `${path}` expressions. Never embed JavaScript, Python, handlers,
  shell commands, or arbitrary templates.
- Declare complex input constraints with sibling `<schema for="name">` Draft 2020-12 JSON
  Schema and keep its `type` consistent with the `<input>` attribute.
- Define explicit `<outputs>` and reference guaranteed top-level step results.
- Set node `timeout` and retry/backoff policy where an external capability can stall.
- Compile the file, then register it with `adoption_tool` (`action="register"`, `module="workflow"`, `artifact_path` = its absolute path).

## Improving an existing one

Locate the Workflow-owned defect in the run trace before changing orchestration. Preserve
compatible public inputs/outputs and use typed parameters instead of copies for individual
benchmark cases. Keep semantic version metadata and status consistent with the staged change;
use the actual returned registration version for evidence. Compile and check boundedness,
reachability, capability names and output references before native execution.

## Evaluating one

Add these checks to the common evaluation:

1. Inspect the exact version, declared inputs/outputs, applicability and available node
   capabilities. Compile and check bounded termination, reachability and retry/verification
   behavior; static checks cannot establish successful execution.
2. Run representative inputs through the registered Workflow and compare with the previous
   version or manual orchestration. Cover changed branches and a failing child operation;
   verify retries terminate and final outputs match actual node results. Exercise authorized
   stateful nodes in isolated state, following the common evaluation rules.
3. Record version-scoped run evidence with `adoption_tool action=record_workflow_evaluation`.
   A success needs a real terminal `run_id`; a static failure needs `case_id`. Include actual
   success, quality (0.0–1.0), elapsed time, token cost and concrete observations.
4. These Workflow run records supplement the shared `record_decision` and consumer-use
   procedure; recording a quality score does not adopt the candidate or repair it.

The runtime's health summary uses at least 3 evaluations, success rate at least 0.8 and
average quality at least 0.7. This is a health indicator, not permission to skip required
coverage or treat a failed safety/termination case as passing.
