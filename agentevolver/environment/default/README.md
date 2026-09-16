---
name: environment_default
description: "The shipped ECP environments. Each is one directory holding an Environment subclass with @action-registered actions and an ENVIRONMENT.md written for the model; a service module is added only by environments that maintain an external connection."
version: 1.0.0
type: collection
category: environment
requirements: []
metadata: {}
---
# Built-in environments

One directory per environment. Every directory here is an `Environment` subclass with
actions a model can call — if it has no actions, it is not an environment and does not
belong here.

## What a directory must contain

| File | | |
|---|---|---|
| `environment.py` | **required** | The `Environment` subclass. Its `@environment_manager.action` methods are what a model can call, and `name` must be a class field — registration reads it off the class, so passing it to a constructor registers as `None`. |
| `ENVIRONMENT.md` | **required** | The environment's rules and its actions' arguments, written for the model. `environment_manager.get_instruction()` returns this body verbatim, so it is what reaches the prompt — not a summary rebuilt from the registry. |
| `README.md` | **required** | The contributor's view: what this environment talks to, and what it costs to run. |
| `__init__.py` | **required** | Exports the class. |

## What a directory may contain

Nothing else is expected, and the optional files are optional for a reason rather than by
oversight.

**`service.py`** — only when the environment maintains an external connection with its own
lifecycle. `ssh/` keeps a ControlMaster socket alive across calls; `browser/` owns a
Playwright session. `computer/` and `artifact_renderer/` act on this machine and have
nothing to keep, so they have no service module and should not grow one to look uniform.

The split is not cosmetic: `environment.py` answers *what a model can ask for*, and
`service.py` answers *how a connection to the far side is maintained*. Folding a live
connection into the action methods is what makes an environment impossible to test without
the far side present.

**A domain module** — `ssh/hosts.py` holds the set of machines this environment can reach
and where that set is kept. Concepts an environment genuinely owns get their own file
rather than swelling `environment.py`.

## The shipped set

| | Actions | Talks to | Service |
|---|---|---|---|
| [`ssh/`](ssh/README.md) | 19 | A remote machine over one persistent SSH connection | yes |
| [`browser/`](browser/README.md) | 12 | A Playwright browser, headful or headless | yes |
| [`computer/`](computer/README.md) | 12 | This machine's screen, keyboard and mouse | no |
| [`artifact_renderer/`](artifact_renderer/README.md) | 2 | The local renderer for HTML-native artifacts | no |
