---
name: model_anthropic
description: "Adapts Anthropic chat requests and responses to AgentEvolver's provider-neutral Message and Model contracts. `serializer.py` owns wire conversion; `chat.py` owns the provider client."
version: 0.1.0
type: provider
category: model
requirements: []
metadata:
  tracks_package_version: true
---
# Anthropic model provider

Adapts Anthropic chat requests and responses to AgentEvolver's provider-neutral Message and
Model contracts. `serializer.py` owns wire conversion; `chat.py` owns the provider client.
