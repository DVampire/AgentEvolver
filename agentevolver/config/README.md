---
name: config
description: "Loads Python configuration files into the global `config` object and validates assembled framework configuration before runtime initialization."
version: 0.1.0
type: module
category: config
requirements: []
metadata:
  tracks_package_version: true
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
