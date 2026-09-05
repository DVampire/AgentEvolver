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
| `types.py` | Client-facing request and event contracts |
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

## One port for pages and deployments

The loopback-only sites gateway is a separate, persistent service on **9876**.
Forward this port once in SSH/VS Code and open `http://localhost:9876/sites/`.
It lists Agent run monitors, benchmark monitors, and deployed websites. They all
use the same routing contract: `/s/<registered-site-name>/`. Internal ports remain
implementation details; `SiteRecord.url` is the backend address, while
`deployment_manager.public_urls(record)` supplies stable browser-facing links.
Setting `GATEWAY_PUBLIC_BASE` changes the advertised origin (for example, an
authenticated HTTPS reverse proxy); it does not provision a domain or TLS.

`ensure_site_gateway()` reuses an identified service or starts the sites-only
entry point detached from the experiment. It refuses an unrelated occupant of
9876. Agent teardown does not stop it. The full interactive gateway mounts the
same router; neither mode needs another port for each monitor. Deploy registries
merge only locally changed records, and the gateway refreshes changed registry
files to discover deployments created by other processes.

The relay supports HTTP methods, streaming responses, WebSocket text/binary
frames, prefix-scoped redirects and cookie paths. It uses the registered backend
URL, including container port mappings, not an arbitrary client-supplied target.
The page index returns names and status only, never deployment sources, env vars,
or complete deployment requests. It does not start stopped applications.

### Application contract

Applications serve their internal routes at `/`. Browser-facing resource, API,
and socket URLs must include the external prefix. Deploy provides
`BASE_PATH=/s/<site-name>/` at build/start; HTTP requests also carry
`X-Forwarded-Prefix` (important for archived releases with a different name).
Use a framework's base-path configuration or a prefix-aware URL helper. Merely
placing a root-absolute `/api/...` URL behind a proxy does not make it prefix-aware.
The gateway deliberately does not rewrite arbitrary JavaScript. Both shipped
monitor templates use prefix-compatible assets and API requests.

This is a trusted, single-user development entry point, not a multi-tenant
hosting boundary. Paths on one origin share browser security state. Keep it on
loopback behind SSH; use authentication and separate site origins when serving
untrusted applications or multiple users publicly. The model-request inspector
can contain private task content and must not be exposed as a public website.
