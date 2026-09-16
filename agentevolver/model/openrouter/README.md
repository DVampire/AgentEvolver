---
name: model_openrouter
description: "Implements OpenRouter chat and REST integration plus provider-compatible serialization. Provider quirks remain localized here so Agents continue using the neutral Model API."
version: 1.0.0
type: provider
category: model
requirements: []
metadata: {}
---
# OpenRouter model provider

Implements OpenRouter chat and REST integration plus provider-compatible serialization. Provider quirks remain localized here so Agents continue using the neutral
Model API.

| Path | Surface |
|---|---|
| `chat.py` | `/v1/chat/completions` — the default; streams token by token |
| `rest.py` | Direct REST client used where the SDK does not fit |
| `serializer.py` | Message and tool conversion shared by the above |
