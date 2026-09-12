"""A directory-shaped component is loaded as a package, so its own files can see each other.

A plugin's shape is `plugin.py` beside one `PluginTool` per file under `tools/`, and the
entry point reaches them the only way Python offers: `from .tools.readability import X`.
That import never worked. Extensions load under invented names like
`ext.plugin.text_metrics` so two versions never collide in `sys.modules`, and nothing
called `ext` exists anywhere — which Python does not mind for a plain module, since it only
looks the name up, but minds a great deal the moment code inside asks for something
relative, because the first thing it does is import the parent.

So every generated plugin died at registration with `No module named 'ext'`, after the run
had written the whole thing. The type had never been loadable, and nothing said so.

Two fixes, and both are needed — fixing either alone moves the error rather than removing
it. The parents are created as empty packages, and the module registers itself in
`sys.modules` *before* its code runs, which is what the real import system does and what
makes a package visible to the imports inside it.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from agentevolver.dynamic import dynamic_manager
from agentevolver.plugins.types import Plugin
from agentevolver.tool.types import Tool


@pytest.fixture
def plugin_tree(tmp_path):
    """The shape the generate skill tells a run to produce."""
    root = tmp_path / "text_metrics"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "_shared.py").write_text("SEPARATOR = '::'\n", encoding="utf-8")
    (root / "tools" / "readability.py").write_text(
        textwrap.dedent("""
        from agentevolver.plugins.types import PluginTool
        from ._shared import SEPARATOR

        class ReadabilityTool(PluginTool):
            name: str = "readability"
            description: str = "Reports how hard a text is to read."
            marker: str = SEPARATOR
    """),
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        textwrap.dedent("""
        from agentevolver.plugins.types import Plugin
        from .tools.readability import ReadabilityTool

        class TextMetricsPlugin(Plugin):
            name: str = "text_metrics"
            description: str = "Measurable properties of a piece of text."
            child: str = ReadabilityTool.model_fields["name"].default
    """),
        encoding="utf-8",
    )
    yield root
    for name in [m for m in sys.modules if m.startswith("ext.")]:
        sys.modules.pop(name, None)


def test_a_directory_component_can_import_its_own_files(plugin_tree):
    """The failure this file exists for, at the layer that produced it."""
    cls = dynamic_manager.load_class_from_path(
        str(plugin_tree / "plugin.py"),
        base_class=Plugin,
        context="plugin",
        module_name="ext.plugin.text_metrics",
        package_dir=str(plugin_tree),
    )
    assert cls.__name__ == "TextMetricsPlugin"
    assert cls.model_fields["child"].default == "readability"


def test_a_sibling_can_import_a_sibling(plugin_tree):
    """One level deeper, and the case a shared helper module needs.

    `tools/readability.py` imports `tools/_shared.py`. If only the top module were made a
    package, this second-level relative import would fail exactly as the first one did.
    """
    dynamic_manager.load_class_from_path(
        str(plugin_tree / "plugin.py"),
        base_class=Plugin,
        context="plugin",
        module_name="ext.plugin.text_metrics",
        package_dir=str(plugin_tree),
    )
    loaded = sys.modules["ext.plugin.text_metrics.tools.readability"]
    assert loaded.ReadabilityTool.model_fields["marker"].default == "::"


def test_the_invented_parent_packages_are_created(plugin_tree):
    """`ext` and `ext.plugin` exist nowhere on disk, so this manager has to make them."""
    dynamic_manager.load_class_from_path(
        str(plugin_tree / "plugin.py"),
        base_class=Plugin,
        context="plugin",
        module_name="ext.plugin.text_metrics",
        package_dir=str(plugin_tree),
    )
    for parent in ("ext", "ext.plugin"):
        assert parent in sys.modules, f"{parent} was never created"
        assert hasattr(sys.modules[parent], "__path__"), f"{parent} is not a package"


def test_the_module_is_visible_to_the_imports_inside_it(plugin_tree):
    """Registration happens before execution, which is the half that is easy to miss.

    With the parents created but the module itself registered afterwards, the error simply
    moves: `No module named 'ext'` becomes `No module named 'ext.plugin.text_metrics'`,
    reported while the module of that exact name is being built.
    """
    dynamic_manager.load_class_from_path(
        str(plugin_tree / "plugin.py"),
        base_class=Plugin,
        context="plugin",
        module_name="ext.plugin.text_metrics",
        package_dir=str(plugin_tree),
    )
    module = sys.modules["ext.plugin.text_metrics"]
    assert module.__path__ == [str(plugin_tree)]
    assert module.__package__ == "ext.plugin.text_metrics"


def test_a_single_file_component_is_not_made_a_package(tmp_path):
    """A tool is one file and has no siblings; giving it a `__path__` would be a lie.

    It would also make `from .anything import x` inside a tool resolve against whatever
    directory it happened to be read from.
    """
    source = tmp_path / "adder_tool.py"
    source.write_text(
        textwrap.dedent("""
        from agentevolver.tool.types import Tool

        class AdderTool(Tool):
            name: str = "adder_tool"
            description: str = "Adds numbers."
    """),
        encoding="utf-8",
    )
    try:
        dynamic_manager.load_class_from_path(
            str(source), base_class=Tool, context="tool", module_name="ext.tool.adder_tool"
        )
        module = sys.modules["ext.tool.adder_tool"]
        assert not hasattr(module, "__path__")
    finally:
        sys.modules.pop("ext.tool.adder_tool", None)


def test_code_that_fails_to_execute_leaves_nothing_behind(tmp_path):
    """A half-built module in `sys.modules` is worse than no module at all.

    The next load of that name finds it, skips execution because it is already there, and
    hands back a class that was never defined — so a syntax error in generated code
    surfaces later as a missing attribute somewhere unrelated. Registering before execution
    is what creates this hazard, so it is handled where it is created.
    """
    source = tmp_path / "broken.py"
    source.write_text("raise RuntimeError('generated code blew up')\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        dynamic_manager.load_class_from_path(
            str(source), base_class=Tool, context="tool", module_name="ext.tool.broken"
        )

    assert "ext.tool.broken" not in sys.modules, (
        "a module that failed to execute stayed registered; the next load would reuse it"
    )


# --------------------------------------------------------------------------- #
# Who decides that a component is a directory
# --------------------------------------------------------------------------- #
class _Captured(Exception):
    """Stops the load once the arguments are known, before anything registers."""

    def __init__(self, kwargs):
        self.kwargs = kwargs


@pytest.fixture
def capture_load(monkeypatch):
    """Intercept the loader call the extension manager makes, and stop there.

    The tests above drive `load_class_from_path` directly, so they say nothing about who
    passes `package_dir` — mutations that made *every* component a package, or none of
    them, left all six of them green. The decision lives in `extension/server.py`, and it
    is the decision that has to be right: a plugin that is not loaded as a package cannot
    import its own tools, and a tool that is loaded as one gets a `__path__` pointing at
    whatever directory it was read from.
    """
    from agentevolver.dynamic import dynamic_manager as dm

    def _capture(*args, **kwargs):
        raise _Captured(kwargs)

    monkeypatch.setattr(dm, "load_class_from_path", _capture)
    return None


def _package_dir_for(module: str, path) -> object:
    """What the extension manager would load `path` as, for `module`."""
    import asyncio

    from agentevolver.extension import extension_manager

    try:
        asyncio.run(
            extension_manager._load_class_component(
                module, str(path), version=None, config=None, return_version=False
            )
        )
    except _Captured as captured:
        return captured.kwargs.get("package_dir")
    raise AssertionError("the loader was never called")


def test_a_directory_component_is_loaded_as_a_package(tmp_path, capture_load):
    """`plugin` and `environment` keep their class in an entry file inside a directory."""
    for module, entry in (("plugin", "plugin.py"), ("environment", "environment.py")):
        root = tmp_path / module / "thing"
        root.mkdir(parents=True)
        (root / entry).write_text("# entry\n", encoding="utf-8")
        assert _package_dir_for(module, root) == str(root), (
            f"{module} is a directory component and must load as a package"
        )


def test_a_single_file_component_is_loaded_as_a_plain_module(tmp_path, capture_load):
    """A tool, an agent and a memory system are each one file with no siblings."""
    for module in ("tool", "agent", "memory"):
        source = tmp_path / f"{module}_thing.py"
        source.write_text("# component\n", encoding="utf-8")
        assert _package_dir_for(module, source) is None, (
            f"{module} is a single file; a package root would be invented for it"
        )


@pytest.mark.asyncio
async def test_environment_upgrade_isolates_helpers_and_rollback(tmp_path, monkeypatch):
    """A helper-only change must reach native calls without changing retained v1 objects."""
    from unittest.mock import AsyncMock
    from agentevolver.environment.context import EnvironmentContextManager
    from agentevolver.environment.server import EnvironmentManagerServer
    import agentevolver.environment.server as environment_server
    from agentevolver.extension.server import ExtensionManagerServer
    from agentevolver.permission import permission_manager
    from agentevolver.version import version_manager

    versions = iter(("1.0.0", "1.0.1"))
    monkeypatch.setattr(version_manager, "get_version", AsyncMock(
        side_effect=lambda module, name: next(versions) if module == "environment" else "1.0.0",
    ))
    monkeypatch.setattr(version_manager, "register_version", AsyncMock())
    monkeypatch.setattr(version_manager, "get_version_history", AsyncMock(return_value=None))
    monkeypatch.setattr(type(permission_manager), "register", lambda self, **kwargs: None)
    context = EnvironmentContextManager(base_dir=str(tmp_path / "env-log"))
    server = EnvironmentManagerServer()
    server.environment_context_manager = context
    monkeypatch.setattr(environment_server, "environment_manager", server)
    extensions = ExtensionManagerServer(base_dir=str(tmp_path / "extensions"))
    entry = textwrap.dedent('''
        from agentevolver.environment.types import Environment
        from agentevolver.environment.server import environment_manager
        from .core import VALUE

        class RevisionProbe(Environment):
            name: str = "revision_probe"
            description: str = "Probe the active package revision."
            metadata: dict = {}
            enable_evolving: bool = True

            @environment_manager.action(name="read", read_only=True)
            async def read(self, ctx=None):
                from .late import VALUE as late_value
                return {"eager": VALUE, "late": late_value}
    ''')
    initial_modules = set(sys.modules)
    try:
        old_instance = None
        for revision in ("1.0.0", "1.0.1"):
            # Mirrors immutable admission: same artifact name and unchanged entry file,
            # different source root even when version is not allocated until registration.
            root = tmp_path / revision / "revision_probe"
            root.mkdir(parents=True)
            (root / "environment.py").write_text(entry)
            for helper in ("core.py", "late.py"):
                (root / helper).write_text(f"VALUE = {revision!r}\n")
            await extensions._load_class_component(
                "environment", str(root), version=None, config={}, return_version=False,
            )
            current = context._environment_configs["revision_probe"]
            assert current.version == revision
            if old_instance is None:
                old_instance = current.instance
            else:
                assert type(old_instance).__module__ != current.cls.__module__
                assert await context("revision_probe", "read", {}) == {
                    "eager": revision, "late": revision,
                }
                # Its first late import happens AFTER v2 has been loaded and called.
                assert await old_instance.read() == {"eager": "1.0.0", "late": "1.0.0"}
        await context.restore("revision_probe", "1.0.0", auto_initialize=False)
        assert await context("revision_probe", "read", {}) == {"eager": "1.0.0", "late": "1.0.0"}
        restored = context._environment_configs["revision_probe"]
        historical = context._environment_history_versions["revision_probe"]["1.0.0"]
        assert restored is not historical
        assert historical.instance is old_instance
    finally:
        for name in set(sys.modules) - initial_modules:
            if name.startswith("ext."):
                dynamic_manager.unload_module(name)
