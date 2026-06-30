"""ExtensionManager — loads hot-pluggable extensions from a flat `extension/` tree.

Framework code lives in `src/` (immutable). Evolved/generated components live outside
`src/`, under a flat working tree:

    extension/
    ├── manifest.json                 # active set: name -> active version + file
    ├── tool/<name>.py                # active source (normal, flat paths)
    ├── agent/<name>.py
    ├── prompt/<name>.html
    ├── skill/<name>/SKILL.md
    ├── environment/<name>.py
    └── .versions/<module>/<name>/<version>.<ext>   # archive: every version coexists

Authoring writes the flat active file; ExtensionManager archives each registered
version into `.versions/` so multiple versions of the same component coexist on disk,
and records the active version per component in `manifest.json`. Rollback copies an
archived version back over the active file and re-registers.

It is deliberately thin: loading is delegated to `dynamic_manager`, registration to
each `*_manager`, and per-component version numbering to `version_manager`.
"""

import os
import shutil
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.logger import logger
from src.utils import assemble_project_path
from src.utils.file_utils import file_lock
from src.extension.types import Manifest, ManifestComponent

# Modules whose components are class-based (loaded via dynamic_manager).
_CLASS_MODULES = {"tool", "agent", "environment"}
# All modules the extension tree may carry.
_MODULES = ["tool", "agent", "prompt", "skill", "environment"]
# Active-file extension per module ("" => the component is a directory, e.g. skills).
_EXT = {"tool": ".py", "agent": ".py", "environment": ".py", "prompt": ".html", "skill": ""}

_ARCHIVE = ".versions"


class ExtensionManagerServer(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default="", description="Root directory of the extension tree")

    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.base_dir = assemble_project_path(base_dir or "extension")
        os.makedirs(self.base_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def module_dir(self, module: str) -> str:
        return os.path.join(self.base_dir, module)

    def stage_path(self, module: str, filename: str) -> str:
        """Absolute path of the flat active file/dir a generator should write to."""
        mdir = self.module_dir(module)
        os.makedirs(mdir, exist_ok=True)
        return os.path.join(mdir, filename)

    def _archive_dir(self, module: str, name: str) -> str:
        return os.path.join(self.base_dir, _ARCHIVE, module, name)

    def _manifest_path(self) -> str:
        return os.path.join(self.base_dir, "manifest.json")

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------
    def read_manifest(self) -> Manifest:
        path = self._manifest_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return Manifest.model_validate_json(f.read())
        return Manifest()

    def _write_manifest(self, manifest: Manifest) -> None:
        with open(self._manifest_path(), "w", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))

    # ------------------------------------------------------------------
    # Cold start
    # ------------------------------------------------------------------
    async def initialize(self) -> Manifest:
        """Load + register the active extension set.

        Prefers the manifest (loads each component at its recorded active version);
        falls back to scanning the flat module dirs on a fresh install. Call after the
        component managers have initialized their built-ins, so extensions layer on top.
        """
        manifest = self.read_manifest()
        if manifest.components:
            loaded: List[ManifestComponent] = []
            for comp in manifest.components:
                abspath = os.path.join(self.base_dir, comp.file)
                if not os.path.exists(abspath):
                    logger.warning(f"| ⚠️ ExtensionManager: active file missing for {comp.module}:{comp.name} ({abspath}); skipping.")
                    continue
                try:
                    await self._load_component(comp.module, abspath, comp.name, version=comp.version, config=None)
                    self._ensure_archived(comp.module, comp.name, abspath, comp.version)
                    loaded.append(comp)
                except Exception as e:
                    logger.error(f"| ❌ ExtensionManager: failed to load {comp.module}:{comp.name}: {e}")
            manifest.components = loaded
            self._write_manifest(manifest)
            logger.info(f"| ✅ ExtensionManager: loaded {len(loaded)} active extension components.")
            return manifest

        # Fresh install: scan flat dirs and register whatever is present.
        return await self._scan_and_load()

    async def _scan_and_load(self) -> Manifest:
        manifest = Manifest()
        for module in _MODULES:
            mdir = self.module_dir(module)
            if not os.path.isdir(mdir):
                continue
            ext = _EXT[module]
            for entry in sorted(os.listdir(mdir)):
                if entry.startswith(".") or entry == "__init__.py":
                    continue
                abspath = os.path.join(mdir, entry)
                if module == "skill":
                    if not (os.path.isdir(abspath) and os.path.exists(os.path.join(abspath, "SKILL.md"))):
                        continue
                elif not (entry.endswith(ext) and os.path.isfile(abspath)):
                    continue
                try:
                    name = await self._load_component(module, abspath, None, version=None, config=None)
                    comp = self._record(module, name, abspath, manifest)
                    self._ensure_archived(module, name, abspath, comp.version)
                except Exception as e:
                    logger.error(f"| ❌ ExtensionManager: failed to load {module}:{entry}: {e}")
        self._write_manifest(manifest)
        if manifest.components:
            logger.info(f"| ✅ ExtensionManager: discovered + loaded {len(manifest.components)} extension components.")
        else:
            logger.info("| 📦 ExtensionManager: no extension components found.")
        return manifest

    # ------------------------------------------------------------------
    # Authoring: hot-add / evolve a single component
    # ------------------------------------------------------------------
    async def add_component(self, module: str, abspath: str, config: Optional[dict] = None) -> str:
        """Register an already-written flat active file, archive its version, update the manifest.

        Returns the registered component name. The version is assigned by the owning
        manager (via version_manager), so re-adding an existing component evolves it.
        """
        name, version = await self._load_component(module, abspath, None, version=None, config=config, return_version=True)
        # Serialize the manifest read-modify-write so parallel add_component calls
        # (e.g. concurrent component evolution) don't lose each other's updates.
        async with file_lock(self._manifest_path()):
            manifest = self.read_manifest()
            comp = self._record(module, name, abspath, manifest, version=version)
            self._ensure_archived(module, name, abspath, comp.version)
            self._write_manifest(manifest)
        logger.info(f"| ➕ ExtensionManager: added {module}:{name} v{comp.version}")
        return name

    async def unload(self, module: str, name: str) -> bool:
        """Unregister an active component and drop it from the manifest (archive kept)."""
        ok = await self._unload_component(module, name)
        async with file_lock(self._manifest_path()):
            manifest = self.read_manifest()
            manifest.remove(module, name)
            self._write_manifest(manifest)
        return ok

    async def deactivate_all(self) -> None:
        async with file_lock(self._manifest_path()):
            manifest = self.read_manifest()
            for comp in list(manifest.components):
                await self._unload_component(comp.module, comp.name)
            self._write_manifest(Manifest())
        logger.info("| 🧹 ExtensionManager: deactivated all extensions.")

    async def reload(self) -> Manifest:
        """Re-load + re-register the active set (e.g. after editing flat files)."""
        manifest = self.read_manifest()
        for comp in manifest.components:
            abspath = os.path.join(self.base_dir, comp.file)
            if os.path.exists(abspath):
                try:
                    await self._load_component(comp.module, abspath, comp.name, version=comp.version, config=None)
                except Exception as e:
                    logger.error(f"| ❌ ExtensionManager: reload failed for {comp.module}:{comp.name}: {e}")
        return manifest

    # ------------------------------------------------------------------
    # Versioning: list / rollback
    # ------------------------------------------------------------------
    def list_component_versions(self, module: str, name: str) -> List[str]:
        adir = self._archive_dir(module, name)
        if not os.path.isdir(adir):
            return []
        ext = _EXT[module]
        out = []
        for entry in os.listdir(adir):
            if module == "skill":
                if os.path.isdir(os.path.join(adir, entry)):
                    out.append(entry)
            elif entry.endswith(ext):
                out.append(entry[: -len(ext)] if ext else entry)
        return sorted(out)

    async def rollback(self, module: str, name: str, version: str, config: Optional[dict] = None) -> str:
        """Restore an archived version over the active file and re-register it."""
        ext = _EXT[module]
        archived = os.path.join(self._archive_dir(module, name), f"{version}{ext}")
        if not os.path.exists(archived):
            raise FileNotFoundError(f"No archived {module}:{name} version '{version}' at {archived}")

        # Determine the active flat destination (reuse the manifest's file if known).
        comp = self.read_manifest().find(module, name)
        if comp:
            dest = os.path.join(self.base_dir, comp.file)
        else:
            dest = os.path.join(self.module_dir(module), f"{name}{ext}")

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if module == "skill":
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(archived, dest)
        else:
            shutil.copyfile(archived, dest)

        loaded = await self._load_component(module, dest, name, version=version, config=config)
        async with file_lock(self._manifest_path()):
            manifest = self.read_manifest()
            self._record(module, loaded, dest, manifest, version=version)
            self._write_manifest(manifest)
        logger.info(f"| ⏪ ExtensionManager: rolled back {module}:{name} to v{version}")
        return loaded

    # ------------------------------------------------------------------
    # Internal: manifest record + archive
    # ------------------------------------------------------------------
    def _record(self, module: str, name: str, abspath: str, manifest: Manifest, version: Optional[str] = None) -> ManifestComponent:
        rel = os.path.relpath(abspath, self.base_dir)
        if version is None:
            existing = manifest.find(module, name)
            version = existing.version if existing else "1.0.0"
        comp = ManifestComponent(module=module, name=name, version=version, file=rel)
        manifest.upsert(comp)
        return comp

    def _ensure_archived(self, module: str, name: str, abspath: str, version: str) -> None:
        ext = _EXT[module]
        adir = self._archive_dir(module, name)
        os.makedirs(adir, exist_ok=True)
        dest = os.path.join(adir, f"{version}{ext}")
        try:
            if module == "skill":
                if os.path.abspath(abspath) != os.path.abspath(dest):
                    if os.path.exists(dest):
                        shutil.rmtree(dest)
                    shutil.copytree(abspath, dest)
            else:
                if os.path.abspath(abspath) != os.path.abspath(dest):
                    shutil.copyfile(abspath, dest)
        except Exception as e:
            logger.warning(f"| ⚠️ ExtensionManager: archiving {module}:{name} v{version} failed: {e}")

    # ------------------------------------------------------------------
    # Per-module load / unload dispatch
    # ------------------------------------------------------------------
    async def _load_component(self, module: str, abspath: str, name_hint: Optional[str],
                              version: Optional[str], config: Optional[dict], return_version: bool = False):
        if module in _CLASS_MODULES:
            return await self._load_class_component(module, abspath, version, config, return_version)
        if module == "prompt":
            return await self._load_prompt(abspath, return_version)
        if module == "skill":
            return await self._load_skill(abspath, version, return_version)
        raise ValueError(f"Unknown extension module: {module}")

    async def _load_class_component(self, module: str, abspath: str, version: Optional[str],
                                    config: Optional[dict], return_version: bool):
        from src.dynamic import dynamic_manager
        base_cls = self._base_class(module)
        stem = os.path.splitext(os.path.basename(abspath))[0]
        module_name = f"ext.{module}.{stem}"
        cls = dynamic_manager.load_class_from_path(
            abspath, base_class=base_cls, context=module, module_name=module_name
        )
        cls.__source_file__ = abspath
        with open(abspath, "r", encoding="utf-8") as f:
            code = f.read()

        if module == "tool":
            from src.tool.server import tool_manager
            cfg = await tool_manager.register(tool=cls, config=config or {}, code=code, override=True, version=version)
        elif module == "agent":
            from src.agent.server import agent_manager
            cfg = await agent_manager.register(agent_cls=cls, agent_config_dict=config, override=True, version=version)
        elif module == "environment":
            from src.environment.server import environment_manager
            cfg = await environment_manager.register(env_cls=cls, env_config_dict=config, override=True, version=version)
        else:
            raise ValueError(f"Not a class-based module: {module}")
        name = getattr(cfg, "name", None) or getattr(cls, "__name__", "")
        return (name, getattr(cfg, "version", version or "1.0.0")) if return_version else name

    async def _load_prompt(self, abspath: str, return_version: bool):
        from src.prompt.server import prompt_manager
        from src.prompt.types import parse_prompt_file
        cfg = parse_prompt_file(abspath)
        if not cfg.name:
            stem = os.path.splitext(os.path.basename(abspath))[0]
            cfg = cfg.model_copy(update={"name": stem})
        registered = await prompt_manager.register(prompt=cfg.model_dump(), override=True)
        return (registered.name, getattr(registered, "version", "1.0.0")) if return_version else registered.name

    async def _load_skill(self, abspath: str, version: Optional[str], return_version: bool):
        from src.skill.server import skill_manager
        cfg = await skill_manager.register(skill_dir=abspath, override=True, version=version)
        name = getattr(cfg, "name", os.path.basename(abspath))
        return (name, getattr(cfg, "version", version or "1.0.0")) if return_version else name

    async def _unload_component(self, module: str, name: str) -> bool:
        try:
            manager = self._manager(module)
            ok = await manager.unregister(name)
            logger.info(f"| 🧹 ExtensionManager: unregistered {module}:{name}")
            return bool(ok)
        except Exception as e:
            logger.warning(f"| ⚠️ ExtensionManager: failed to unregister {module}:{name}: {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _base_class(module: str):
        if module == "tool":
            from src.tool.types import Tool
            return Tool
        if module == "agent":
            from src.agent.types import Agent
            return Agent
        if module == "environment":
            from src.environment.types import Environment
            return Environment
        return None

    @staticmethod
    def _manager(module: str):
        if module == "tool":
            from src.tool.server import tool_manager
            return tool_manager
        if module == "agent":
            from src.agent.server import agent_manager
            return agent_manager
        if module == "prompt":
            from src.prompt.server import prompt_manager
            return prompt_manager
        if module == "skill":
            from src.skill.server import skill_manager
            return skill_manager
        if module == "environment":
            from src.environment.server import environment_manager
            return environment_manager
        raise ValueError(f"Unknown extension module: {module}")


# Global singleton
extension_manager = ExtensionManagerServer()
