"""Installing what an evolution run produced — one hook, all eight component types.

Every type used to have a hook of its own, and the files agreed on the whole algorithm:
read the run's reasoning for the path it wrote, promote that path out of the staged
extension root, hand it to ``extension_manager.add_component``, and turn any failure into
a BLOCK so the run can fix the artifact and call ``done_tool`` again. What differed was
five decisions, and the copies disagreed on them by accident as often as on purpose — one
type had no hook at all, so a generated plugin could never be installed, and only the
workflow copy read a path out of backticks, which is how agents actually write them.

Splitting by type was never necessary, because the run already says which type it built:
``generate_agent`` and its two siblings carry ``target_type`` in their context, and that
is what selects a row of ``_SHAPES`` here. So there is one hook, and the eight rows below
are the entire difference between installing a tool and installing a workflow.

A row answers up to five questions; six of the eight answer none of them:

============  ==============================================================
``directory`` whether the artifact is a directory rather than a file
``entry``     for a directory holding a class, the file the loader reads
``suffix``    the file extension, for the file case
``config``    what the component is constructed with
``prepare``   what to do to the artifact before it is promoted
``after``     what registers alongside it, once it is in
``recover``   what a run that wrote no primary artifact still means
============  ==============================================================
"""

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.registry import HOOK
from agentevolver.sandbox.project import staged_extension_root

#: How a run names a path. An agent writes one of two ways — bare, or fenced in backticks
#: or quotes inside a sentence — and a whitespace split only reads the first. The second
#: is not a nicety: ``review workflow.html`` split on spaces yields two halves, neither of
#: which exists, so the artifact is written, reported as created, and never registered.
_CANDIDATE = re.compile(r"""(?P<quote>[`'"])(?P<quoted>[^`'"]+)(?P=quote)|(?P<bare>\S+)""")


# --------------------------------------------------------------------------- #
# The five answers a type may give
# --------------------------------------------------------------------------- #
def _evolving(_extra: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Register it as evolvable, so a later round can optimize what this one made."""
    return {"enable_evolving": True}


def _agent_config(extra: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An agent is constructed, not merely loaded: it needs a workspace and a model."""
    from agentevolver.config import config
    return {
        "base_dir": config.workspace_root,
        "model_name": extra.get("model_name") or "",
        "enable_evolving": True,
    }


def _no_config(_extra: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A workflow carries ``enable-evolving`` as an attribute of its own root, written by
    ``_activate_and_compile``. Passing it again as construction config would be a second
    place for the same fact to be wrong."""
    return None


def _as_written(path: str) -> str:
    """Register the artifact exactly as the run left it."""
    return path


def _activate_and_compile(path: str) -> str:
    """Mark a workflow active and evolvable, compile it, and rewrite it atomically.

    Compiling before the write is the point: an artifact that does not compile is never
    left on disk in an active state, and the failure reaches the run as a BLOCK naming
    what is wrong with the HTML.
    """
    from lxml import html

    from agentevolver.workflow import workflow_compiler

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
    return path


async def _register_sibling_prompt(name: str, _extra: Dict[str, Any]) -> None:
    """A tool-calling agent has an HTML prompt beside it. Non-fatal if it fails.

    The class is already registered by this point; refusing the whole run because its
    prompt did not take would discard a working agent over a template.
    """
    from agentevolver.extension import extension_manager
    html_path = extension_manager.stage_path("prompt", f"{name}.html")
    if not os.path.exists(html_path):
        return
    try:
        await extension_manager.add_component("prompt", html_path)
        logger.info(f"| 🔄 registration_hook: prompt '{name}' registered")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"| ⚠️  registration_hook: prompt registration failed (non-fatal): {e}")


async def _prompt_only_change(extra: Dict[str, Any]) -> Optional[HookResult]:
    """A prompt-only evolution wrote no ``.py``, and is still a complete change.

    An optimizer's remit is the agent class, its prompt, or both. Blocking the run for a
    file it never needed to write would reject the change for succeeding at the narrower
    thing it set out to do.
    """
    html_path = resolve_artifact(
        module="prompt", suffix=".html", target_name=extra.get("target_name"),
        reasoning=extra.get("reasoning") or "",
        extension_root=staged_extension_root(),
        matches=lambda c: "extension/" in c and "/prompt/" in c and c.endswith(".html"),
    )
    if not html_path:
        return None
    try:
        from agentevolver.extension import extension_manager
        name = await extension_manager.add_component("prompt", html_path)
        logger.info(f"| 🔄 registration_hook: prompt '{name}' registered from {html_path}")
        return HookResult.allow()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"| ⚠️  registration_hook: {e}")
        return HookResult.block(
            f"[registration failed] {e}\nPlease fix the prompt and call done_tool again."
        )


@dataclass(frozen=True)
class Shape:
    """What installing one component type *does* differently from installing any other.

    Behaviour only. What the artifact looks like on disk — directory or file, entry file,
    extension — is a fact about the type, not about installing it, and lives on the
    capability table where the sandbox's promotion reads the same answer. Held here as
    well, the two drifted: promotion's copy stopped at six modules while this one had
    eight, so three types could be registered and never promoted.
    """

    config: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]] = _evolving
    prepare: Callable[[str], str] = _as_written
    after: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None
    recover: Optional[Callable[[Dict[str, Any]], Awaitable[Optional[HookResult]]]] = None


#: The types whose installation differs from the default at all. Everything else — six of
#: the eight — takes `Shape()` unchanged, which is why this is a lookup with a default
#: rather than one row per type: a row that says nothing is a row that can go stale.
SHAPES: Dict[str, Shape] = {
    "agent": Shape(
        config=_agent_config, after=_register_sibling_prompt, recover=_prompt_only_change,
    ),
    "workflow": Shape(config=_no_config, prepare=_activate_and_compile),
}

#: The default, for the six types that answer none of the four questions.
DEFAULT_SHAPE = Shape()


def shape_for(module: str) -> Optional[Shape]:
    """How to install ``module``, or ``None`` when it is not an evolvable component.

    The set of installable types comes from the capability table, so a ninth type is
    installable the moment it is declared there — with default behaviour, which is right
    for six of the eight that exist.
    """
    from agentevolver.capability.types import component_type

    if component_type(module) is None:
        return None
    return SHAPES.get(module, DEFAULT_SHAPE)


# --------------------------------------------------------------------------- #
# Finding what the run wrote
# --------------------------------------------------------------------------- #
def resolve_artifact(
    *,
    module: str,
    directory: bool = False,
    entry: str = "",
    suffix: str = ".py",
    target_name: Optional[str],
    artifact_path: Optional[str] = None,
    reasoning: str = "",
    extension_root: str = "",
    matches: Optional[Callable[[str], bool]] = None,
) -> Optional[str]:
    """Find the artifact a run wrote, in the three places a run can name it.

    In order: the structured ``artifact_path`` the run passed, any path it mentioned in
    its reasoning, and finally the staged path implied by ``target_name``. Only the middle
    one is filtered by ``matches`` — prose mentions source files, references and examples
    alongside the artifact, so a mention has to look like *this* type's output before it
    is believed, while a structured field is the run stating which file it means and needs
    no guessing.

    Args:
        module: Extension module the artifact belongs to, also the path segment.
        directory: Whether the artifact is a directory rather than a file.
        entry: For a directory holding a Python class, the file the loader reads.
        suffix: File extension, for the file case.
        target_name: Component name, used for the staged-path fallback.
        artifact_path: A path the run passed as data rather than prose.
        reasoning: The run's ``done_tool`` reasoning.
        extension_root: Root that relative paths are resolved against, and the first
            of the two staging roots the ``target_name`` fallback looks in.
        matches: Predicate deciding whether a prose mention could be this artifact.

    Returns:
        An existing path, or ``None`` when nothing resolves.
    """
    def accept(path: str) -> Optional[str]:
        """The path this run produced, or ``None`` if it is not there.

        A file must exist. A directory must exist, and if the type declares an entry
        file, must contain it — an ``environment/`` holding no ``environment.py`` is a
        directory the loader would fail on later rather than here.
        """
        if not directory:
            return path if os.path.isfile(path) else None
        path = path.rstrip("/")
        if entry and path.endswith(".py"):
            path = os.path.dirname(path)
        if not os.path.isdir(path):
            return None
        if entry and not os.path.isfile(os.path.join(path, entry)):
            return None
        return path

    def absolute(candidate: str) -> str:
        return (candidate if os.path.isabs(candidate)
                else os.path.join(extension_root, candidate.removeprefix("extension/")))

    if artifact_path:
        resolved = accept(absolute(str(artifact_path).strip("`'\" ")))
        if resolved:
            return resolved

    for match in _CANDIDATE.finditer(reasoning):
        candidate = (match.group("quoted") or match.group("bare") or "").strip("`'\".,;:()")
        if not candidate or (matches and not matches(candidate)):
            continue
        resolved = accept(absolute(candidate))
        if resolved:
            return resolved

    if target_name:
        from agentevolver.extension import extension_manager
        leaf = target_name if directory else f"{target_name}{suffix}"
        # Both staging roots, nearest first. `extension_root` is the tree the prompt told
        # this run to write into; `stage_path` is the configured root. They are the same
        # path in a plain run and diverge whenever a session stages its own extension
        # tree — and then a run that did exactly as instructed had its artifact declared
        # missing, because only the root it was never shown was searched.
        for candidate in (
            os.path.join(extension_root, module, leaf) if extension_root else "",
            extension_manager.stage_path(module, leaf),
        ):
            resolved = accept(candidate) if candidate else None
            if resolved:
                return resolved
    return None


def _mentions(module: str, entry: Any) -> Callable[[str], bool]:
    """Whether a path mentioned in prose could be this type's artifact.

    Both halves earn their place. Without ``extension/``, a run that quotes the source
    file it copied from gets that file registered. Without the module segment, a run that
    names its skill's ``references/tool.md`` while generating a skill resolves to the
    wrong module entirely.
    """
    def matches(candidate: str) -> bool:
        if "extension/" not in candidate or f"/{module}/" not in candidate:
            return False
        return entry.directory or candidate.endswith(entry.suffix)

    return matches


# --------------------------------------------------------------------------- #
# The hook
# --------------------------------------------------------------------------- #
@HOOK.register_module(force=True)
class RegistrationHook(Hook):
    """Resolve one generated artifact, promote it, register it — whatever type it is."""

    name: str = "registration_hook"
    description: str = (
        "Registers what an evolution run generated — tool, skill, agent, connector, "
        "memory, plugin, workflow or environment — as a live extension."
    )
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        """Locate the generated artifact, promote it if staged, and register it.

        Fired after an evolution run calls ``done_tool``. Registers newly generated
        components as evolvable so a later round can optimize them; overwriting a *frozen*
        entity is still refused inside ``add_component``.

        Args:
            ctx: Hook context whose ``input`` carries ``target_type``, ``target_name``,
                ``artifact_path``, ``reasoning``, ``extension_root`` and ``model_name``.

        Returns:
            ``HookResult.allow()`` on success, or ``HookResult.block(reason)`` when the
            type is unknown, the artifact cannot be located, or registration fails.
        """
        from agentevolver.capability.types import component_type

        extra = ctx.input or {}
        module = str(extra.get("target_type") or "")
        entry = component_type(module)
        shape = shape_for(module)
        if entry is None or shape is None:
            # Read from the run's context rather than guessed from its output: a generate
            # run's target does not exist yet, so nothing can be looked up, and a wrong
            # guess searches the wrong module — which surfaces as "could not locate the
            # generated file" rather than as the type being wrong.
            from agentevolver.capability.types import COMPONENT_TYPES
            known = ", ".join(sorted(e.type for e in COMPONENT_TYPES))
            msg = (f"target_type must be one of {known}; got {module!r}. Without it there "
                   f"is no way to know what was built or how to install it.")
            logger.warning(f"| ⚠️  registration_hook: {msg}")
            return HookResult.block(f"[registration failed] {msg}")

        target_name: Optional[str] = extra.get("target_name")
        # This run's staging tree, from the layout table. Passed in the payload before,
        # which meant the dispatcher and the agent's prompt each resolved it separately —
        # and a run whose prompt named the shared tree wrote where this could not find it.
        extension_root: str = staged_extension_root()
        noun = "directory" if entry.directory else "file"

        path = resolve_artifact(
            module=module, directory=entry.directory, entry=entry.entry,
            suffix=entry.suffix, target_name=target_name,
            artifact_path=extra.get("artifact_path"),
            reasoning=extra.get("reasoning") or "", extension_root=extension_root,
            matches=_mentions(module, entry),
        )
        if not path:
            recovered = await shape.recover(extra) if shape.recover else None
            if recovered is not None:
                return recovered
            msg = f"Could not locate generated {module} {noun} for '{target_name}' in reasoning."
            logger.warning(f"| ⚠️  registration_hook: {msg}")
            return HookResult.block(
                f"[registration failed] {msg}\nInclude the {module} {noun} path in "
                f"done_tool reasoning and call done_tool again."
            )

        from agentevolver.sandbox.project import is_staged_extension_root, validate_staged_extension
        staged = is_staged_extension_root(extension_root)
        if staged:
            validate_staged_extension(extension_root)

        try:
            # Before promotion, deliberately: a type that rewrites its own artifact does
            # it to the staged copy, so what gets promoted is what was validated.
            path = shape.prepare(path)
            if staged:
                from agentevolver.hook.promotion import promote_approved_component
                path = promote_approved_component(extension_root, path)

            from agentevolver.extension import extension_manager
            name = await extension_manager.add_component(
                module, path, config=shape.config(extra)
            )
            logger.info(f"| 🔄 registration_hook: '{name}' promoted and registered from {path}")
            if shape.after:
                await shape.after(name, extra)
            return HookResult.allow()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️  registration_hook: {e}")
            return HookResult.block(
                f"[registration failed] {e}\nPlease fix the issue and call done_tool again."
            )


__all__ = ["DEFAULT_SHAPE", "RegistrationHook", "SHAPES", "Shape",
           "resolve_artifact", "shape_for"]
