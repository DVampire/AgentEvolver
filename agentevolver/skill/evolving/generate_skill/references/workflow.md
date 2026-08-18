# Writing a workflow

## What it is

A workflow is one HTML file, `{extension_root}/workflow/{name}.html`, containing a
`<workflow>` element inside a complete `<html>` document with a DOCTYPE.

**The contract**: it compiles, and its declared inputs and outputs stay stable — canvas nodes and
other workflows already target them by name.

One methodology for the complete Workflow lifecycle. A Workflow is a reusable orchestration
program, not an Agent subtype and not a substitute for a Skill's domain instructions.

## Creating a Workflow

- Inspect active Workflows first and avoid semantic duplicates.
- Generalize concrete task details into typed `<inputs>`.
- Write one complete HTML document to `extension/workflow/{name}.html`.
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
- Compile the file before completion and include its absolute path in `done_tool.reasoning`.
