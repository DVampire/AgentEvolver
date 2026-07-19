---
name: agentevolver
description: "This directory contains the runtime Python package. Each first-level functional module owns a `README.md` describing its responsibility, public entry points, important files, and architectural boundary."
version: 0.1.0
type: package
category: framework
requirements: []
metadata:
  tracks_package_version: true
---
# AgentEvolver package

This directory contains the runtime Python package. Each first-level functional module
owns a `README.md` describing its responsibility, public entry points, important files,
and architectural boundary.

## Documentation convention

- A module without an independent protocol tracks the package version.
- A module with a persisted or external contract declares its own contract version.
- `default/`, provider, UI, and bundled Skill script directories are implementation
  subdivisions documented by their owning first-level module unless they expose an
  independently versioned artifact format.
- Public imports should normally come from a module's `__init__.py`; `server.py` owns the
  stable manager facade, `context.py` owns registry/lifecycle state, and `types.py` owns
  data contracts where that pattern applies.

The package entry points are `cli.py`, the shared `registry.py`, and the managers exported
by the modules below.
