---
name: message
description: "Defines provider-neutral conversation messages, multimodal content parts, tool calls, and function-call payloads."
version: 0.1.0
type: module
category: message
requirements: []
metadata:
  tracks_package_version: true
---
# Message

Defines provider-neutral conversation messages, multimodal content parts, tool calls, and
function-call payloads.

All contracts live in `types.py`. Provider serializers in `model/` translate these types to
wire formats; Message itself contains no transport or model-specific behavior.
