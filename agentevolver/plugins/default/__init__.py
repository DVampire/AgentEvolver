"""Default provider plugins.

Each plugin is now a **package** under this directory (e.g. ``yahoo/``,
``fmp/``, and the migrated Langflow bundles). Importing a plugin package runs
its ``@PLUGIN.register_module`` decorators, registering the plugin(s) it
contains. Discovery is automatic — drop a new plugin package here and it is
picked up with no edit to this file.
"""

import importlib
import pkgutil

__all__: list = []

for _module in pkgutil.iter_modules(__path__):
    if _module.name.startswith("_"):
        continue
    _mod = importlib.import_module(f"{__name__}.{_module.name}")
    # Re-export any registered Plugin subclasses for ``from ...default import *``.
    for _name in getattr(_mod, "__all__", []):
        globals()[_name] = getattr(_mod, _name, None)
        __all__.append(_name)
