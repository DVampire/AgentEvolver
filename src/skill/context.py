"""Skill Context Manager for loading, managing, and serving skills."""

import os
import json
import re
import shutil
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.logger import logger
from src.config import config
from src.skill.types import SkillConfig
from src.response.types import Response, ResponseType
from src.session import SessionContext
from src.skill.types import SkillContext
from src.utils import assemble_project_path, file_lock, render_capability_card
from src.version import version_manager
from src.permission import permission_manager, PermissionMode


class SkillContextManager(BaseModel):
    """Manages the lifecycle of skills: discovery, loading, registration, update, and execution."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="Base directory for skill runtime data")
    default_skills_dir: str = Field(default=None, description="Directory for built-in default skills")
    extension_skills_dir: str = Field(default=None, description="Directory for generated/user skills")

    _skill_configs: Dict[str, SkillConfig] = {}
    _skill_history_versions: Dict[str, Dict[str, SkillConfig]] = {}

    def __init__(
        self,
        base_dir: Optional[str] = None,
        default_skills_dir: Optional[str] = None,
        extension_skills_dir: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        if base_dir is not None:
            self.base_dir = assemble_project_path(base_dir)
        else:
            self.base_dir = assemble_project_path(os.path.join(config.default_dir, "skill"))
        os.makedirs(self.base_dir, exist_ok=True)



        _src_dir = Path(__file__).resolve().parent
        # Built-in skills live in the default/ dir; extension skills are managed
        # externally (loaded by ExtensionManager into the active version).
        self.default_skills_dir = default_skills_dir or str(_src_dir / "default")
        self.extension_skills_dir = extension_skills_dir or assemble_project_path(os.path.join("extension", "skill"))

        self._skill_configs: Dict[str, SkillConfig] = {}
        self._skill_history_versions: Dict[str, Dict[str, SkillConfig]] = {}
        # get_instruction cache: (allowlist, types) -> text; cleared on registry change.
        self._instr_cache: Dict[Any, str] = {}

        logger.info(f"| 📁 Skill context manager base directory: {self.base_dir}")

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self, skill_names: Optional[List[str]] = None):
        """Discover and load skills from default and persisted sources.

        Args:
            skill_names: If provided, only load these skills.
        """
        discovered: Dict[str, SkillConfig] = {}

        # 1. Load from built-in default directory
        default_configs = await self._load_from_directory(Path(self.default_skills_dir))
        discovered.update(default_configs)

        # 1b. Load from extension directory (generated/user skills); extension overrides default
        Path(self.extension_skills_dir).mkdir(parents=True, exist_ok=True)
        extension_configs = await self._load_from_directory(Path(self.extension_skills_dir))
        discovered.update(extension_configs)


        # 3. Filter by name if requested
        if skill_names is not None:
            filtered: Dict[str, SkillConfig] = {}
            for name in skill_names:
                if name in discovered:
                    filtered[name] = discovered[name]
                else:
                    logger.warning(f"| ⚠️ Requested skill '{name}' not found in discovered skills")
            discovered = filtered

        # 4. Build text representations, register versions, and store
        for name, skill_config in discovered.items():
            skill_config.text = self._build_text_representation(skill_config)
            self._skill_configs[name] = skill_config

            if name not in self._skill_history_versions:
                self._skill_history_versions[name] = {}
            self._skill_history_versions[name][skill_config.version] = skill_config

            await version_manager.register_version("skill", name, skill_config.version)

            permission_manager.register(
                entity_name=name,
                mode=PermissionMode(skill_config.permission_mode),
            )
            logger.info(f"| 🎯 Skill '{name}' v{skill_config.version} loaded from {skill_config.skill_dir}")

        # 5. Persist
        self._invalidate_instruction()

        logger.info(f"| ✅ Skills initialization completed — {len(self._skill_configs)} skill(s) loaded")

    # ------------------------------------------------------------------
    # Directory scanning & SKILL.md parsing
    # ------------------------------------------------------------------

    async def _load_from_directory(self, root_dir: Path) -> Dict[str, SkillConfig]:
        """Scan *root_dir* for sub-directories that contain a SKILL.md file."""
        configs: Dict[str, SkillConfig] = {}

        if not root_dir.exists():
            logger.info(f"| 📂 Skill directory does not exist, skipping: {root_dir}")
            return configs

        for child in sorted(root_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                skill_config = self._parse_skill_dir(child)
                configs[skill_config.name] = skill_config
            except Exception as e:
                logger.error(f"| ❌ Failed to parse skill at {child}: {e}")

        return configs

    def _parse_skill_dir(self, skill_dir: Path) -> SkillConfig:
        """Parse a single skill directory into a SkillConfig."""
        skill_md = skill_dir / "SKILL.md"
        raw = skill_md.read_text(encoding="utf-8")

        frontmatter, body = self._parse_frontmatter(raw)

        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")
        version = frontmatter.get("version", "1.0.0")
        require_grad = str(frontmatter.get("require_grad", "false")).lower() == "true"
        type_value = frontmatter.get("type", "tool")
        metadata = {k: v for k, v in frontmatter.items() if k not in ("name", "description", "version", "type", "require_grad")}

        def _scan_dir(d: Path) -> List[str]:
            return [str(p) for p in sorted(d.rglob("*")) if p.is_file()] if d.is_dir() else []

        scripts = _scan_dir(skill_dir / "scripts")
        resources = _scan_dir(skill_dir / "resources")
        references = _scan_dir(skill_dir / "references")
        examples = _scan_dir(skill_dir / "examples")

        return SkillConfig(
            name=name,
            description=description,
            metadata=metadata,
            require_grad=require_grad,
            version=version,
            type=type_value,
            skill_dir=str(skill_dir),
            content=body.strip(),
            scripts=scripts,
            resources=resources,
            references=references,
            examples=examples,
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
        """Split YAML frontmatter (between --- delimiters) from the markdown body."""
        pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
        match = pattern.match(text)

        if not match:
            return {}, text

        yaml_block = match.group(1)
        body = text[match.end():]

        # Parse the frontmatter as real YAML so structured values (e.g. a multi-label
        # `type: [orchestrator, worker]` list) are preserved rather than stringified.
        try:
            frontmatter = yaml.safe_load(yaml_block) or {}
            if not isinstance(frontmatter, dict):
                frontmatter = {}
        except yaml.YAMLError as e:
            logger.warning(f"| ⚠️ Failed to parse SKILL.md frontmatter as YAML: {e}")
            frontmatter = {}

        return frontmatter, body

    # ------------------------------------------------------------------
    # Text representation (for prompt injection)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_text_representation(skill_config: SkillConfig) -> str:
        """Build a concise summary for prompt injection (no full SKILL.md body)."""
        parts = [
            f"Skill: {skill_config.name}",
            f"Description: {skill_config.description}",
            f"Type: {', '.join(skill_config.type_tags)}",
            f"Version: {skill_config.version}",
            f"Skill Directory: {skill_config.skill_dir}",
            f"SKILL.md: {os.path.join(skill_config.skill_dir, 'SKILL.md')}",
        ]

        if skill_config.resources:
            parts.append("Resources:")
            for r in skill_config.resources:
                parts.append(f"  - {r}")

        if skill_config.scripts:
            parts.append("Scripts:")
            for s in skill_config.scripts:
                parts.append(f"  - {s}")

        if skill_config.references:
            parts.append("References:")
            for r in skill_config.references:
                parts.append(f"  - {r}")

        if skill_config.examples:
            parts.append("Examples:")
            for e in skill_config.examples:
                parts.append(f"  - {e}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Register / Update / Unregister / Copy / Restore
    # ------------------------------------------------------------------

    async def register(
        self,
        skill_dir: str,
        override: bool = False,
        version: Optional[str] = None,
    ) -> SkillConfig:
        """Register a skill from a directory containing SKILL.md.

        Args:
            skill_dir: Path to the skill directory.
            override: If True, overwrite an existing skill with the same name.
            version: Explicit version string. If None, reads from frontmatter or auto-generates.

        Returns:
            The registered SkillConfig.
        """
        skill_dir_path = Path(skill_dir)
        if not (skill_dir_path / "SKILL.md").exists():
            raise FileNotFoundError(f"No SKILL.md found in {skill_dir}")

        skill_config = self._parse_skill_dir(skill_dir_path)

        if version is not None:
            skill_config.version = version
        else:
            existing_version = await version_manager.get_version("skill", skill_config.name)
            if existing_version and skill_config.version == "1.0.0":
                skill_config.version = existing_version

        if skill_config.name in self._skill_configs and not override:
            raise ValueError(
                f"Skill '{skill_config.name}' already registered. Use override=True or update()."
            )

        skill_config.text = self._build_text_representation(skill_config)
        self._skill_configs[skill_config.name] = skill_config

        if skill_config.name not in self._skill_history_versions:
            self._skill_history_versions[skill_config.name] = {}
        self._skill_history_versions[skill_config.name][skill_config.version] = skill_config

        await version_manager.register_version("skill", skill_config.name, skill_config.version)

        self._invalidate_instruction()

        logger.info(f"| 📝 Registered skill: {skill_config.name} v{skill_config.version}")
        return skill_config

    async def update(
        self,
        name: str,
        skill_dir: Optional[str] = None,
        new_version: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SkillConfig:
        """Update an existing skill and create a new version.

        You can either point to a new skill_dir (re-parse SKILL.md) or update
        individual fields (description, content, metadata) in-place.

        Args:
            name: Name of the skill to update.
            skill_dir: If provided, re-parse this directory as the new skill source.
            new_version: Explicit new version. If None, auto-increments patch.
            description: Override description text.
            content: Override SKILL.md body content.
            metadata: Override metadata dict.

        Returns:
            Updated SkillConfig.
        """
        original = self._skill_configs.get(name)
        if original is None:
            raise ValueError(f"Skill '{name}' not found. Use register() first.")

        if skill_dir is not None:
            updated = self._parse_skill_dir(Path(skill_dir))
        else:
            updated = SkillConfig(**original.model_dump())

        if description is not None:
            updated.description = description
        if content is not None:
            updated.content = content
        if metadata is not None:
            updated.metadata = metadata

        if new_version is None:
            new_version = await version_manager.generate_next_version("skill", name, "patch")
        updated.version = new_version

        updated.text = self._build_text_representation(updated)
        self._skill_configs[name] = updated

        if name not in self._skill_history_versions:
            self._skill_history_versions[name] = {}
        self._skill_history_versions[name][new_version] = updated

        await version_manager.register_version(
            "skill", name, new_version,
            description=description or f"Updated from {original.version}",
        )

        self._invalidate_instruction()

        logger.info(f"| 🔄 Updated skill '{name}' from v{original.version} to v{new_version}")
        return updated

    async def unregister(self, name: str) -> bool:
        """Remove a skill from the active set.

        Args:
            name: Skill name to unregister.

        Returns:
            True if removed, False if not found.
        """
        if name not in self._skill_configs:
            logger.warning(f"| ⚠️ Skill '{name}' not found")
            return False

        version = self._skill_configs[name].version
        del self._skill_configs[name]

        self._invalidate_instruction()

        logger.info(f"| 🗑️ Unregistered skill '{name}' v{version}")
        return True

    async def copy(
        self,
        name: str,
        new_name: Optional[str] = None,
        new_version: Optional[str] = None,
        new_skill_dir: Optional[str] = None,
    ) -> SkillConfig:
        """Copy an existing skill, optionally under a new name.

        Args:
            name: Source skill name.
            new_name: Name for the copy. If None, keeps the original name.
            new_version: Version for the copy. If None, auto-generates.
            new_skill_dir: If provided, physically copies the skill directory.

        Returns:
            The new SkillConfig.
        """
        original = self._skill_configs.get(name)
        if original is None:
            raise ValueError(f"Skill '{name}' not found")

        if new_name is None:
            new_name = name

        copied = SkillConfig(**original.model_dump())
        copied.name = new_name

        if new_skill_dir is not None:
            dest = Path(new_skill_dir)
            if not dest.exists():
                shutil.copytree(original.skill_dir, str(dest))
            copied.skill_dir = str(dest)

        if new_version is None:
            if new_name == name:
                new_version = await version_manager.generate_next_version("skill", new_name, "patch")
            else:
                new_version = await version_manager.get_version("skill", new_name)
        copied.version = new_version

        copied.text = self._build_text_representation(copied)
        self._skill_configs[new_name] = copied

        if new_name not in self._skill_history_versions:
            self._skill_history_versions[new_name] = {}
        self._skill_history_versions[new_name][new_version] = copied

        await version_manager.register_version(
            "skill", new_name, new_version,
            description=f"Copied from {name}@{original.version}",
        )

        self._invalidate_instruction()

        logger.info(f"| 📋 Copied skill '{name}' v{original.version} -> '{new_name}' v{new_version}")
        return copied

    async def restore(self, name: str, version: str) -> Optional[SkillConfig]:
        """Restore a specific version of a skill from history.

        Args:
            name: Skill name.
            version: Version string to restore.

        Returns:
            Restored SkillConfig, or None if version not found.
        """
        version_map = self._skill_history_versions.get(name, {})
        target = version_map.get(version)
        if target is None:
            logger.warning(f"| ⚠️ Version {version} not found for skill '{name}'")
            return None

        restored = SkillConfig(**target.model_dump())
        restored.text = self._build_text_representation(restored)
        self._skill_configs[name] = restored

        version_history = await version_manager.get_version_history("skill", name)
        if version_history:
            if version not in version_history.versions:
                await version_manager.register_version("skill", name, version)
            version_history.current_version = version
        else:
            await version_manager.register_version("skill", name, version)


        logger.info(f"| 🔄 Restored skill '{name}' to v{version}")
        return restored

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    async def get(self, skill_name: str) -> Optional[SkillConfig]:
        """Get a loaded skill config by name."""
        return self._skill_configs.get(skill_name)

    async def get_info(self, skill_name: str) -> Optional[SkillConfig]:
        """Alias for get()."""
        return self._skill_configs.get(skill_name)

    async def list(self) -> List[str]:
        """Return names of all loaded skills."""
        return list(self._skill_configs.keys())

    # ------------------------------------------------------------------
    # Context generation (for agent prompt)
    # ------------------------------------------------------------------

    async def get_instruction(self, allowlist: Optional[List[str]] = None, types: Optional[List[str]] = None) -> str:
        """Assemble the skill instruction text for prompt injection, on demand.

        `allowlist` (list of skill names) selects which skills to include: None = all;
        [] = none; [names] = only those. `types` filters by frontmatter ``type``
        (``["worker"]`` / ``["orchestrator"]``) — the hard guardrail keeping worker SOPs
        out of the MetaAgent and orchestration recipes out of workers. Cached per
        (allowlist, types); invalidated on registry change via `_invalidate_instruction`.
        """
        key = (None if allowlist is None else tuple(allowlist),
               None if types is None else tuple(types))
        if key in self._instr_cache:
            return self._instr_cache[key]
        targets = list(self._skill_configs.keys()) if allowlist is None else allowlist
        parts: List[str] = []
        for name in targets:
            cfg = self._skill_configs.get(name)
            if cfg is None:
                continue
            if types and not any(t in types for t in cfg.type_tags):
                continue
            detail = [f"- **SKILL.md**: {os.path.join(cfg.skill_dir, 'SKILL.md')}"]
            if cfg.scripts:
                detail.append(f"- **Scripts**: {', '.join(cfg.scripts)}")
            if cfg.references:
                detail.append(f"- **References**: {', '.join(cfg.references)}")
            if cfg.resources:
                detail.append(f"- **Resources**: {', '.join(cfg.resources)}")
            if cfg.examples:
                detail.append(f"- **Examples**: {', '.join(cfg.examples)}")
            parts.append(render_capability_card(
                name=cfg.name,
                description=cfg.description or "",
                meta=f"`[{', '.join(cfg.type_tags)}]` v{cfg.version}",
                body="\n".join(detail),
            ))
        text = "\n\n".join(parts)
        self._instr_cache[key] = text
        return text

    def _invalidate_instruction(self) -> None:
        """Drop cached instructions so the next get_instruction rebuilds."""
        self._instr_cache.clear()

    # ------------------------------------------------------------------
    # Contract (persistent text summary)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Persistence (JSON) — with version history
    # ------------------------------------------------------------------

    async def __call__(
        self,
        name: str,
        input: Dict[str, Any],
        ctx: SessionContext = None,
        **kwargs,
    ) -> Response:
        """Execute a skill by returning its SKILL.md as instructions for the calling agent."""
        skill_config = self._skill_configs.get(name)
        if skill_config is None:
            return Response(type=ResponseType.SKILL, 
                success=False,
                message=f"Skill '{name}' not found. Available skills: {list(self._skill_configs.keys())}",
            )

        logger.info(f"| 🎯 Executing skill '{name}' v{skill_config.version} with input: {input}")

        skill_dir = skill_config.skill_dir
        content = skill_config.content.replace("python scripts/", f"python {skill_dir}/scripts/")
        parts = [content]

        if skill_config.resources:
            parts.append(f"\nAvailable resources: {', '.join(skill_config.resources)}")
        if skill_config.scripts:
            parts.append(f"\nAvailable scripts: {', '.join(skill_config.scripts)}")
        if skill_config.references:
            parts.append(f"\nReference docs: {', '.join(skill_config.references)}")
        if skill_config.examples:
            parts.append(f"\nExamples: {', '.join(skill_config.examples)}")

        instructions = "\n".join(parts)
        logger.info(f"| ✅ Skill '{name}' — returned instructions ({len(instructions)} chars)")

        return Response(type=ResponseType.SKILL, 
            success=True,
            message=instructions,
            data={
                "skill_name": name,
                "type": skill_config.type,
                "version": skill_config.version,
                "input": input,
                "skill_dir": skill_dir,
            },
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self):
        """Release all loaded skills."""
        self._skill_configs.clear()
        self._skill_history_versions.clear()
        logger.info("| 🧹 Skill context manager cleaned up")
