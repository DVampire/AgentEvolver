---
name: environment_default_browser
description: "Implements the stateful browser Environment and its backing service. `ENVIRONMENT.md` is the machine-readable registration document; `environment.py` defines actions and `service.py` owns browser-service integration."
version: 1.0.0
type: module
category: environment
requirements: []
metadata: {}
---
# Browser environment

Implements the stateful browser Environment and its backing service. `ENVIRONMENT.md` is the
machine-readable registration document; `environment.py` defines actions and `service.py`
owns browser-service integration.

Each session owns its Page, BrowserContext and pending dialog. Browser actions surface a
pending modal promptly without accepting it; `handle_dialog` is an explicit user/Agent
decision. Native async dialog callbacks remain compatible. Cancellation drains operation
tasks; observation failures retain available metadata/DOM and expose errors, not a stale
image presented as current. Screenshots have a 5-second timeout.

Commands validate Python tokens, not text inside JavaScript strings. Keyboard aliases
are normalized; a modifier list is one chord, otherwise entries are successive presses.
When Firecrawl is unconfigured, initialization removes search from both the instance
and the registered action schemas. Known URLs remain accessible through `goto`.
