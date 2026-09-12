# Connector execution checks

Use the [shared conventions](../conventions.md) for the lifecycle, comparison scope,
versioned evidence, repair and adoption. This reference adds MCP and upstream-service
checks; it does not prescribe an extra harness, fixed question count or evaluator agent.

## Structure and discovery

Validate the staged connector directory:

```bash
python {skill_dir}/scripts/connector/validate.py {connector_dir}
python {skill_dir}/scripts/connector/probe.py stdio python {connector_dir}/server.py
```

For an existing remote service, use the declared transport and URL with `probe.py`. Check
startup, negotiated tool names, schemas and effect annotations against `CONNECTOR.md`.
Exposing a focused subset of server tools is valid; every exposed action must exist and
have accurate arguments, effects and documentation. Listing tools does not test execution.

For a local server, check imports and declared dependencies in its real runtime, portable
script paths and clean protocol output (diagnostics on stderr). Use bounded timeouts and
verify that failed startup or disconnects preserve an actionable error.

## Native success and failure

After registration, call the actual exposed connector action through the framework.
Select checks according to the operation and the claimed change:

- Valid request: verify returned values against a trusted small reference, including schema,
  identifiers, timestamps, units and provenance where relevant. Test the required operation,
  not just service status, credentials or `list_tools`.
- Invalid request or upstream error: verify it becomes an observable failed native call,
  using a server tool error or `CallToolResult(isError=True, ...)`. Error text inside a
  successful MCP content block still signals success to the protocol.
- Boundary behavior: exercise affected pagination, empty results, rate limits, timeout,
  decoding or partial-response handling. Do not silently replace missing values with data
  that appears valid.
- Recovery: after fixing an authored defect, register the changed version, retry the failed
  action and verify a different valid case. Preserve the earlier failure evidence.
- Side effects: verify file/state destinations, idempotence and cleanup within authorized
  isolated state. A download that writes a file needs truthful write effects.

When a service is unavailable, fixtures can test parsing and error handling. Label them as
fixtures; they do not prove live service access. Investigate permitted providers or access
methods without assuming the user must host an MCP server. Distinguish unavailable access
from an unimplemented wrapper and from incorrect invocation.

## Comparison and reuse

Compare the candidate's native output and cost with the prior Connector or existing method
on equivalent inputs. Preserve a different reuse/regression case. Snapshot mutable upstream
inputs or record time/version differences so a data change is not credited to code changes.
Save expected/observed results and actual call IDs in the work record outside the candidate.

For a model-selection or reasoning claim, use available authorized fresh consumers under
the common rules, with comparable capabilities and budgets. Design realistic questions with
verifiable outcomes; complexity or dozens of calls is not itself evaluation quality. Direct
native-action checks by the current agent are sufficient for deterministic interface claims.

Report coverage, missing evidence and concrete repair findings through the common decision
contract. A successful discovery, a report webpage or graceful rejection of every request
cannot establish a working connector for the original required operation.
