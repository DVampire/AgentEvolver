---
name: protocol
description: "Defines typed agent-to-agent conversations over Runtime delivery. Supported channels include delegation, escalation, progress, control, query, and publish/subscribe."
version: 1.0.0
type: module
category: protocol
requirements: []
metadata: {}
---
# Protocol

Defines typed agent-to-agent conversations over Runtime delivery. Supported channels include
delegation, escalation, progress, control, query, and publish/subscribe.

| File | Responsibility |
|---|---|
| `types.py` | Typed protocol messages |
| `server.py` | Channel routing through `protocol_manager` |

Protocol defines what messages mean; Runtime owns how mailboxes and lifecycle delivery work.

## Publish/subscribe

`SubscriptionEventMessage` is a typed task envelope carrying topic, event type, structured
payload, publisher, and timestamp. Logical topics are automatically prefixed with the root
session identity before subscription/publication, preventing two concurrent task trees from
cross-delivering identically named events.

Use subscriptions for repeated event-driven work by the same long-lived Agent identity:
register a background child with `continuable=true` plus `subscription_topics`, then publish
with `publish_event_tool`. Use ordinary blocking delegation for a one-off request/response and
`send_message_tool` for a targeted follow-up to one known child. Publishing is fan-out and
asynchronous: its count confirms queue acceptance, while results remain in each subscriber job.
