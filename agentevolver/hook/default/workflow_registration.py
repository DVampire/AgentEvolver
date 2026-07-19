"""Register generated/evolved Workflow HTML through the extension lifecycle."""

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class WorkflowRegistrationHook(Hook):
    """Resolve one staged HTML artifact and register it as a live extension."""

    name: str = "workflow_registration_hook"
    description: str = "Validates and registers generated Workflow HTML as an active extension."
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra = ctx.input or {}
        path = self._resolve(
            extra.get("target_name"), extra.get("artifact_path"),
            extra.get("reasoning") or "", extra.get("extension_root") or "",
        )
        if not path:
            return HookResult.block(
                "[registration failed] Could not locate the generated Workflow HTML. "
                "Include its absolute extension/workflow/*.html path in done_tool reasoning."
            )
        try:
            # Match Tool/Skill evolution: a validated registration is live immediately.
            from lxml import html
            from agentevolver.workflow import workflow_compiler
            from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension

            if is_staged_extension_root(extra.get("extension_root") or ""):
                validate_staged_extension(extra["extension_root"])

            source_path = Path(path)
            raw_source = source_path.read_text(encoding="utf-8")
            if not re.match(r"^\s*<!DOCTYPE\s+html", raw_source, re.IGNORECASE):
                raise ValueError("Generated Workflow must be a complete HTML document with DOCTYPE")
            tree = html.fromstring(raw_source)
            if tree.tag.lower() != "html":
                raise ValueError("Generated Workflow root element must be <html>")
            node = tree if tree.tag == "workflow" else tree.find(".//workflow")
            if node is None:
                raise ValueError("HTML must contain a <workflow> element")
            node.set("status", "active")
            node.set("enable-evolving", "true")
            doctype = tree.getroottree().docinfo.doctype
            source = html.tostring(tree, encoding="unicode", pretty_print=True)
            if doctype:
                source = f"{doctype}\n{source}"
            workflow_compiler.compile(source)  # validate before mutating the artifact
            fd, temporary = tempfile.mkstemp(prefix=f".{source_path.name}-", dir=source_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(source)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, source_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

            if is_staged_extension_root(extra.get("extension_root") or ""):
                from agentevolver.hook.promotion import promote_approved_component
                path = promote_approved_component(extra["extension_root"], path)

            from agentevolver.extension import extension_manager
            name = await extension_manager.add_component("workflow", path)
            logger.info(f"| 🔄 WorkflowRegistrationHook: registered active '{name}' from {path}")
            return HookResult.allow()
        except Exception as exc:
            logger.warning(f"| ⚠️ WorkflowRegistrationHook: {exc}")
            return HookResult.block(f"[registration failed] {exc}")

    @staticmethod
    def _resolve(
        target_name: Optional[str], artifact_path: Optional[str], reasoning: str,
        extension_root: str,
    ) -> Optional[str]:
        candidates = [artifact_path] if artifact_path else []
        for match in re.finditer(
            r"(?P<quote>[`'\"])(?P<path>.+?\.html)(?P=quote)|(?P<bare>\S+\.html)",
            reasoning,
        ):
            candidates.append(match.group("path") or match.group("bare"))
        for raw in candidates:
            candidate = str(raw).strip("`'\".,;:()")
            if candidate.endswith(".html") and "workflow" in candidate:
                if not os.path.isabs(candidate):
                    candidate = os.path.join(extension_root, candidate.removeprefix("extension/"))
                if os.path.isfile(candidate):
                    return candidate
        if target_name:
            from agentevolver.extension import extension_manager
            candidate = extension_manager.stage_path("workflow", f"{target_name}.html")
            return candidate if os.path.isfile(candidate) else None
        return None
