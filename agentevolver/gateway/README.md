---
name: gateway
description: "Defines the versioned boundary used by interactive clients to communicate with AgentEvolver. The package uses a lazy public import so importing `agentevolver.gateway` does not eagerly start transport dependencies."
version: 1.0.0
type: module
category: gateway
requirements: []
metadata: {}
---
# Gateway

Defines the versioned boundary used by interactive clients to communicate with AgentEvolver.
The package uses a lazy public import so importing `agentevolver.gateway` does not eagerly
start transport dependencies.

| File | Responsibility |
|---|---|
| `protocol.py` | Client-facing request and event contracts |
| `service.py` | Gateway application service |
| `approval.py` | Session-fenced, one-shot Tool approval rendezvous |
| `transport.py` | Transport adaptation |
| `__main__.py` | Standalone gateway entry point |

Gateway adapts external clients; internal agent messaging belongs to Protocol and Runtime.

## Tool approval rendezvous

When a monotonic Tool guard returns `ToolPolicyDecision.ask(reason)`, the Gateway-installed
resolver creates one immutable pending record and publishes `approval.requested`. The
record binds the dialog to the Tool execution token, call lineage, project, conversation,
task, agent, step, world, argument names, and canonical argument digest. Raw argument
values are deliberately excluded because commands, file content, cookies, and credentials
must not be copied into a UI event or conversation transcript.

The client answers with:

```json
{
  "method": "approval.respond",
  "params": {
    "session_id": "project-id",
    "approval_id": "one-request-id",
    "decision": "allow_once"
  }
}
```

Only `allow_once` and `reject` are valid. The response must name the same project, and the
pending future is consumed exactly once; stale, duplicate, or cross-project responses
return `delivered: false` and never grant authority. `approval.list` returns outstanding
records for a reconnecting client. A timeout publishes `approval.expired` and denies the
call, while Gateway shutdown publishes `approval.cancelled` and releases every waiter as
denied. There is no persistent “always allow” decision in this protocol.

Requested/responded/expired/cancelled decisions are also non-ignorable Trace facts. On an
accepted call, Tool Manager performs its durability checkpoint only after the responded
fact is emitted and immediately before the body. Thus a training/high-risk run cannot
produce an external effect whose consent exists only in a browser or an unwritten queue.

`approval_timeout_seconds` controls the bounded wait and defaults to 300 seconds. A
temporary socket disconnect does not silently reject or approve the request: the record
remains listable until a client reconnects, the configured timeout expires, or the Gateway
itself stops.
