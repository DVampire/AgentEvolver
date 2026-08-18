# Improving a workflow

## What it is

A workflow is one HTML file, `{extension_root}/workflow/{name}.html`, containing a
`<workflow>` element inside a complete `<html>` document with a DOCTYPE.

**The contract**: it compiles, and its declared inputs and outputs stay stable — canvas nodes and
other workflows already target them by name.

## Improving a Workflow

1. Call `inspect_tool` (capability_type="workflow") first. Stop if missing or `enable_evolving=false`.
2. Read evaluation evidence and identify a Workflow-owned defect.
3. Make the smallest structural change; preserve public name and input/output compatibility
   unless the task explicitly authorizes a breaking change.
4. Increment the semantic version and keep status `active`.
5. Compile and check boundedness, reachability, capability names, and output references.
6. Include the edited absolute HTML path in `done_tool.reasoning` for registration.

Never tune a Workflow to one benchmark case. Prefer parameterization over copying variants.
