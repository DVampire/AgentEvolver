---
name: scope
description: "A registration scope owns every registration made through it and removes them as a unit — in reverse order, surviving a failure partway through, and reporting what is still installed. It is what lets one contributor provide several capabilities and still be removable."
version: 1.0.0
type: module
category: scope
requirements: []
metadata: {}
---
# Scope

Each registry here owns one kind of thing and knows how to remove one by name. Nothing
owned the **set** a single contributor installed.

That gap decided something bigger than it looks. `ExtensionManager` removed a component
from the one registry named by its `module` field, which was correct only because a
component was not allowed to be more than one thing. So a contributor could not provide,
say, two tools plus a prompt section — not because anything rejected it, but because
nothing could take it back out.

A scope is that missing handle.

```python
scope = Scope(name="tool:my_extension")
await scope.register(tool_manager, SearchTool())
await scope.register(tool_manager, FetchTool())
await scope.register(prompt_manager, SearchPrompt())

failed = await scope.dispose()      # all three, newest first
```

## What `dispose` guarantees

**Reverse order.** Later registrations may depend on earlier ones — a tool registered
against a connector has to go before the connector does.

**One failure does not stop the rest.** Aborting partway leaves a set nobody can
describe: some removed, some not, no record of which. Failures are collected and
returned as labels, so the caller learns what is still installed.

**Idempotent.** Disposing twice must not unregister a name a later contributor has since
claimed. That is the quiet way this kind of cleanup corrupts a registry — the second
dispose looks like it worked.

## What it does not do

It does not make registration transactional. If the third `register` raises, the first
two are still installed; the caller holds the scope and can dispose it. Rolling back
automatically would mean deciding that a partially-built scope is worthless, and a caller
that wants to retry one registration is better served by keeping the other two.

It also does not reach inside a registry. `unregister(name)` is the whole contract, and a
manager without one is refused at `register` time rather than at disposal — when the
caller can still do something about it.

## Adding a registry to it

Nothing to add. Every manager in this project already spells removal `unregister(name)`,
so `Scope.register` works against all of them. A new manager joins by having that method;
`tests/test_scope.py` checks the ones that exist keep it.
