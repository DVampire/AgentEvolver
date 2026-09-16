---
name: config
description: "Loads Python configuration files into the global `config` object and validates assembled framework configuration before runtime initialization."
version: 1.0.0
type: module
category: config
requirements: []
metadata: {}
---
# Config

Loads Python configuration files into the global `config` object and validates assembled
framework configuration before runtime initialization.

| File | Responsibility |
|---|---|
| `config.py` | Configuration loading, overrides, and path processing |
| `validate.py` | Cross-module assembly validation |

Configuration is declarative input. Managers remain responsible for constructing and
owning their runtime instances.

## Trace integrity profile

`trace_integrity_profile` selects the durability contract for semantic Trace boundaries:

| Value | Behavior |
|---|---|
| `interactive` | Continue after a timeout/failure, but append a non-ignorable `integrity_degraded` fact when Trace is active |
| `training` | Refuse model dispatch, world-changing Tool execution, or step completion unless preceding facts are durable |
| `high_risk` | Uses the same fail-closed durability rule; its distinct name lets deployments attach stricter approval or retention policy |

The global value defaults to `interactive` in `configs/base.py`. A Session can override it
with `ctx.extra["trace_integrity_profile"]`; that value survives Agent → Model/Tool context
conversion. `validate_assembly()` rejects unknown profile names so a typo cannot silently
downgrade a training run to best effort.

## Human approval timeout

`approval_timeout_seconds` bounds how long a Gateway Tool-policy ASK may suspend. It
defaults to `300.0` seconds and must be positive. Timeout is a rejection, not an implicit
approval; the Gateway publishes `approval.expired`, the Tool body is never entered, and
the caller receives the stable `approval_denied` execution code.
