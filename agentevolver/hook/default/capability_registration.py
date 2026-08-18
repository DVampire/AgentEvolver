"""Installing what an evolution run produced, once, for every type shaped like the rest.

Five capability types had a registration hook each, and the five files differed in two
places: the module string handed to ``extension_manager.add_component``, and whether the
artifact is a file or a directory. Everything else — resolving the path out of the run's
reasoning, promoting it out of the staged extension root, the wording of the block message
— was copied. Plugin, which needed the same hook, had none at all, so a generated plugin
could not be installed.

So the algorithm is written once and the five subclasses carry only what differs. Their
names are unchanged: the dispatcher looks a hook up by ``{type}_registration_hook``, and
that is how the evolution agents ask for one.

``agent`` and ``workflow`` keep their own files. An agent registers up to three components
and accepts a prompt-only change with no ``.py`` at all; a workflow rewrites its artifact
and compiles it before anything is registered. Neither is this algorithm with a different
noun, which is the only reason the others could be merged.
"""

import os
from typing import ClassVar, Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK


class CapabilityRegistrationHook(Hook):
    """Resolve one generated artifact, promote it, register it. Not itself registered."""

    #: The extension module this installs into — also the path segment searched for.
    module: ClassVar[str] = ""
    #: Whether the artifact is a directory. Files additionally match on ``.py``.
    directory: ClassVar[bool] = False
    #: For a directory holding a Python class, the entry file the loader reads. The run
    #: may name either the directory or that file — the creator skill says both are
    #: acceptable — and the loader wants the directory, so naming the entry resolves up.
    entry: ClassVar[str] = ""

    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        """Locate the generated artifact, promote it if staged, and register it.

        Fired after an evolution run calls ``done_tool``. Registers newly generated
        components as evolvable so a later round can optimize them; overwriting a
        *frozen* entity is still refused inside ``add_component``.

        Args:
            ctx: Hook context whose ``input`` carries ``target_name``, ``reasoning``
                and ``extension_root``.

        Returns:
            ``HookResult.allow()`` on success, or ``HookResult.block(reason)`` when the
            artifact cannot be located or registration fails.
        """
        extra = ctx.input or {}
        target_name: Optional[str] = extra.get("target_name")
        reasoning: str = extra.get("reasoning") or ""
        extension_root: str = extra.get("extension_root") or ""
        noun = "directory" if self.directory else "file"

        path = self._resolve(target_name, reasoning, extension_root)
        if not path:
            msg = f"Could not locate generated {self.module} {noun} for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  {type(self).__name__}: {msg}")
            return HookResult.block(
                f"[registration failed] {msg}\nInclude the {self.module} {noun} path in "
                f"done_tool reasoning and call done_tool again."
            )

        from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension
        if is_staged_extension_root(extension_root):
            validate_staged_extension(extension_root)
            from agentevolver.hook.promotion import promote_approved_component
            path = promote_approved_component(extension_root, path)

        try:
            from agentevolver.extension import extension_manager
            name = await extension_manager.add_component(
                self.module, path, config={"enable_evolving": True}
            )
            logger.info(f"| 🔄 {type(self).__name__}: '{name}' promoted and registered from {path}")
            return HookResult.allow()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️  {type(self).__name__}: {e}")
            return HookResult.block(f"[registration failed] {e}\nPlease fix the issue and call done_tool again.")

    def _resolve(self, target_name: Optional[str], reasoning: str, extension_root: str) -> Optional[str]:
        """Find the artifact the run wrote.

        Prefers an ``extension/.../{module}/...`` path named in the run's reasoning;
        falls back to the staged path derived from ``target_name``.

        Returns:
            The existing path, or ``None`` if nothing resolvable exists.
        """
        from agentevolver.extension import extension_manager
        for token in reasoning.split():
            token = token.strip(".,;:()")
            if "extension/" not in token or f"/{self.module}/" not in token:
                continue
            if not self.directory and not token.endswith(".py"):
                continue
            candidate = (token if token.startswith("/")
                         else os.path.join(extension_root, token.removeprefix("extension/")))
            resolved = self._accept(candidate)
            if resolved:
                return resolved
        if target_name:
            leaf = target_name if self.directory else f"{target_name}.py"
            return self._accept(extension_manager.stage_path(self.module, leaf))
        return None

    def _accept(self, path: str) -> Optional[str]:
        """The path this run produced, or ``None`` if it is not there.

        A file must exist. A directory must exist, and if the type declares an entry
        file, must contain it — an ``environment/`` holding no ``environment.py`` is a
        directory the loader would fail on later rather than here.
        """
        if not self.directory:
            return path if os.path.exists(path) else None
        path = path.rstrip("/")
        if self.entry and path.endswith(".py"):
            path = os.path.dirname(path)
        if not os.path.isdir(path):
            return None
        if self.entry and not os.path.isfile(os.path.join(path, self.entry)):
            return None
        return path


@HOOK.register_module(force=True)
class ToolRegistrationHook(CapabilityRegistrationHook):
    name: str = "tool_registration_hook"
    description: str = "Registers a generated tool file with tool_manager after generation."
    module: ClassVar[str] = "tool"


@HOOK.register_module(force=True)
class SkillRegistrationHook(CapabilityRegistrationHook):
    name: str = "skill_registration_hook"
    description: str = "Registers a generated skill directory (SKILL.md) after generation."
    module: ClassVar[str] = "skill"
    directory: ClassVar[bool] = True


@HOOK.register_module(force=True)
class ConnectorRegistrationHook(CapabilityRegistrationHook):
    name: str = "connector_registration_hook"
    description: str = "Registers a generated connector directory (CONNECTOR.md) after generation."
    module: ClassVar[str] = "connector"
    directory: ClassVar[bool] = True


@HOOK.register_module(force=True)
class PluginRegistrationHook(CapabilityRegistrationHook):
    """The type that had no hook, so a generated plugin could never be installed."""

    name: str = "plugin_registration_hook"
    description: str = "Registers a generated plugin directory (PLUGIN.md) after generation."
    module: ClassVar[str] = "plugin"
    directory: ClassVar[bool] = True
    entry: ClassVar[str] = "plugin.py"


@HOOK.register_module(force=True)
class EnvironmentRegistrationHook(CapabilityRegistrationHook):
    name: str = "environment_registration_hook"
    description: str = "Registers a generated environment directory (ENVIRONMENT.md) after generation."
    module: ClassVar[str] = "environment"
    directory: ClassVar[bool] = True
    entry: ClassVar[str] = "environment.py"


@HOOK.register_module(force=True)
class MemoryRegistrationHook(CapabilityRegistrationHook):
    name: str = "memory_registration_hook"
    description: str = "Registers a generated memory file with memory_manager after generation."
    module: ClassVar[str] = "memory"


__all__ = [
    "CapabilityRegistrationHook",
    "ConnectorRegistrationHook",
    "EnvironmentRegistrationHook",
    "MemoryRegistrationHook",
    "PluginRegistrationHook",
    "SkillRegistrationHook",
    "ToolRegistrationHook",
]
