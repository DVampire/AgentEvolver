---
name: tool_default_coordination
description: "Thin model-facing adapters over Runtime-owned messaging, reporting, escalation, event publication, and process scope."
version: 1.0.0
type: module
category: tool
requirements: []
metadata: {}
---
# Coordination tools

Thin Tool adapters only. The runtime owns mailboxes, lifecycle, delivery, subscriptions
and the allowlist a process runs under; this package exposes those actions to a model and
holds no coordination logic of its own.

| Tool | The one thing it does |
|---|---|
| `send_message_tool` | Hand a live sub-agent a fresh task, by pid |
| `reply_tool` | Unblock a sub-agent suspended inside `escalate_tool` |
| `escalate_tool` | Ask the parent when blocked, and wait at a safe point |
| `report_tool` | Send progress to the parent without ending the run |
| `publish_event_tool` | Fan an event out to a topic's subscribers |
| `grant_tool` | Widen one running sub-agent's roster by one named capability |

`grant_tool` is here rather than with the evolution tools because what it does is
coordination: it changes what another process is permitted to do. It lived on
`adoption_tool` for a while for the wrong reason — that tool happened to be mounted
where the grant was needed — and every one of `adoption_tool`'s other actions answers a
different question, whether an evolved component stays.
