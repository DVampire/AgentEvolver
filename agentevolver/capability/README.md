---
name: capability
description: "The capability types and the schema protocol they share. `CAPABILITY_TYPES` names every type a model can call and what the framework knows about each; every callable Manager exposes `get_schema(name, action=None, format=\"json\")` and structurally implements `CapabilitySchemaProvider`."
version: 1.0.0
type: module
category: capability
requirements: []
metadata: {}
---
# Capability

A **capability** is something a model can call. This module holds the two things every
capability type shares: what the framework knows about the type, and how one of its
members describes its own call.

| File | Holds |
|---|---|
| `types.py` | `CapabilityType`, `CAPABILITY_TYPES`, `capability_type()`, `AGENT_MOUNT_TYPES` |
| `schema.py` | `CapabilitySchema`, `CapabilitySchemaProvider`, `SchemaFormat`, `SchemaSource` |

## The types

Seven types are projected into the model's native tool list: **tool, skill, connector,
agent, environment, workflow, plugin**. `CAPABILITY_TYPES` is the one place that says so,
and every consumer reads it — `assemble_native_tools` decides which managers to ask, the
plan gate decides what it can rule on, the canvas builds an agent's mount pickers, and
`inspect_capability_tool` resolves a type name to its manager.

They were separate lists, and the way that fails is not hypothetical: a type could be
registered, addressable and callable while being absent from the model's tool list,
because one file had not been told about it.

Two properties do the explaining, and neither is "is it a capability":

**`container`** — whether a name addresses one callable thing or a set of them. A tool is
one function; a connector is a server with actions, an environment an object with actions,
a plugin a service with tools. The container's members are what the model calls, and a
container's route carries the member: `("plugin", "tavily", "tavily_search")`.

**`judgeable`** — whether the type's effects can be read off a declaration. A tool states
`mutates` and `permission_mode` next to the code that knows; an agent or a workflow does
whatever the thing it runs does, which no declaration can state in advance. Plan mode
gates on exactly this.

Registering something is not the same as making it callable. `memory`, `prompt`,
`constraint`, `process`, `knowledge` and `benchmark` have registries and no row here,
because a model never calls one.

## Naming a member

A native function name may not contain a dot and shares one flat namespace, so a
container's member is qualified with `__` — `browser__click`, `tavily__tavily_search`.
The dotted form (`tavily.tavily_search`) is the internal address a canvas node or a
workflow step carries. The two spellings differ on purpose; `QUALIFIED_SEPARATOR` is the
join.

## The schema protocol

Every callable Manager exposes `get_schema(name, action=None, format="json")` and
structurally implements the typed `CapabilitySchemaProvider`. `format="json"` returns the
exact native function-calling object consumed by `function_callings()`; `format="md"`
returns a human-readable contract including schema source and strict/permissive status.
`CapabilitySchema` validates the shared invariants.

Prompt context remains a compact discovery roster. Native schemas are sent separately in
the model request, and `inspect_capability_tool` exposes both Markdown and JSON on demand
— which is why a capability's prompt card does not repeat its parameters (see
`utils.string_utils.instruction_for_prompt`).

Schema sources are `declared`, `inferred`, `remote`, and `legacy_fallback`. A legacy
fallback is intentionally permissive for backward compatibility and must not be described
as a complete contract; new built-ins should always be declared or inferred.
