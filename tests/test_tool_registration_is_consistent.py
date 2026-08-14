"""Registering a tool touches four places that nothing ties together.

A class in `agentevolver/tool/default/`, an export in that package's `__init__`, sometimes
a stub in `configs/tools/`, and a name in every agent config that should see it. Miss the
export and the class is never imported, so the registry never learns it. Miss the name and
the tool exists but the model never sees it — which surfaces as "the agent didn't use it",
about the hardest failure there is to trace back to a missing line.

The stub is deliberately optional: only 18 of the tools have one, because a stub exists to
override a default, not to declare a tool. Asserting one per tool was this file's first
draft and it failed on fourteen tools that were working correctly — the invariant is the
other direction, that an *imported* stub exists.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "agentevolver" / "tool" / "default"
CONFIG_DIR = ROOT / "configs"


def _registered_tools() -> dict:
    """`name` of every `@TOOL.register_module` class, mapped to (module, class).

    Read from the AST rather than by importing: an import error anywhere in the package
    would empty this mapping and turn every check below green by accident.
    """
    found = {}
    for path in sorted(TOOL_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("register_module" in ast.dump(d) for d in node.decorator_list):
                continue
            for stmt in node.body:
                if (isinstance(stmt, ast.AnnAssign)
                        and getattr(stmt.target, "id", None) == "name"
                        and isinstance(stmt.value, ast.Constant)):
                    found[stmt.value.value] = (path.name, node.name)
    return found


REGISTERED = _registered_tools()


def test_the_tools_were_actually_found():
    """A scan that finds nothing passes every check below vacuously."""
    assert len(REGISTERED) > 20, f"only found {sorted(REGISTERED)}"


@pytest.mark.parametrize("tool_name", sorted(REGISTERED))
def test_every_registered_tool_is_exported(tool_name):
    """An unexported class is never imported, so the registry never sees it."""
    module, cls = REGISTERED[tool_name]
    exports = (TOOL_DIR / "__init__.py").read_text(encoding="utf-8")
    assert cls in exports, (
        f"{cls} ({tool_name}) is not exported from tool/default/__init__.py; nothing "
        f"imports it, so it is never registered")


def test_no_config_lists_a_tool_that_does_not_exist():
    """A typo here is silent — the agent simply never gets the tool.

    The run then looks like a model that chose not to use it, which is indistinguishable
    from a model that could not.
    """
    unknown = []
    for path in sorted(CONFIG_DIR.glob("*.py")):
        for name in re.findall(r'^\s*"([a-z0-9_]+_tool)",',
                               path.read_text(encoding="utf-8"), re.M):
            if name not in REGISTERED:
                unknown.append(f"{path.name} -> {name}")
    assert not unknown, "configs name tools that are not registered:\n  " + "\n  ".join(unknown)


def test_every_imported_stub_exists():
    """The direction that actually holds. A missing stub fails at import, not at use."""
    missing = []
    for path in sorted(CONFIG_DIR.glob("*.py")):
        for stub in re.findall(r"^\s*from \.tools\.(\w+) import ",
                               path.read_text(encoding="utf-8"), re.M):
            if not (CONFIG_DIR / "tools" / f"{stub}.py").exists():
                missing.append(f"{path.name} imports configs/tools/{stub}.py, which is absent")
    assert not missing, "\n  ".join(missing)


def test_every_agent_with_bash_can_collect_a_background_job():
    """`run_in_background` without the `job_*` tools starts work nothing can read.

    The agent is handed a job id and no way to use it — worse than not being able to
    background at all, because the capability looks available and silently drops results.
    """
    stranded = []
    for path in sorted(CONFIG_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if '"bash_tool",' not in text:
            continue
        missing = [t for t in ("job_list_tool", "job_output_tool", "job_kill_tool")
                   if f'"{t}",' not in text]
        if missing:
            stranded.append(f"{path.name} lacks {missing}")
    assert not stranded, ("these agents can start background work but not collect it:\n  "
                          + "\n  ".join(stranded))
