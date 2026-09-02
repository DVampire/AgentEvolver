"""What the framework imports at module level, it declares.

Two of these were only ever present transitively. `langchain_mcp_adapters` — every MCP
connector call goes through it — was covered by `mcp`, which does not bring it; and
`pillow`, imported at the top of `screenshot_utils`, came in behind something else. Both
work on a machine where another package happened to install them, and neither works on a
clean one, which is where it is discovered.

Scoped to module-level imports outside the plugin catalog. A plugin deliberately imports
its own service library *inside* the call, so one unavailable dependency fails one tool
rather than stopping the whole plugin from loading; those are the plugin's business and
belong in `[project.optional-dependencies]`, not in the required set.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agentevolver"

#: Import name → distribution name, where the two differ.
DISTRIBUTION = {
    "pil": "pillow",
    "yaml": "pyyaml",
    "sklearn": "scikit_learn",
    "dotenv": "python_dotenv",
    "google": "google_genai",
    "pydantic_core": "pydantic",
    "markdown": "markitdown",
    "pdfminer": "pdfminer.six",
    "camelot": "camelot_py",
    "browser_use": "browser_use",
    "programbench": "programbench",
}

#: Directories whose imports are the component's own business, not the framework's.
EXCLUDED = (
    "/plugins/default/",
    "/skill/",
    "/connector/default/",
    "/benchmark/default/",
    "/environment/default/",
    "/tool/default/web/markdown/",
    "/sandbox/default/",
)


def _declared() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    specs = list(project["dependencies"])
    for group in project.get("optional-dependencies", {}).values():
        specs.extend(group)

    def normalise(spec: str) -> str:
        return re.split(r"[<>=\[!;]", spec)[0].strip().lower().replace("-", "_")

    return {normalise(spec) for spec in specs}


def _top_level_imports() -> dict[str, set[str]]:
    """Third-party modules imported at the top level of framework code."""
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        text = str(path)
        if "__pycache__" in text or any(part in text for part in EXCLUDED):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in tree.body:  # module level only
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top and top not in sys.stdlib_module_names and top != "agentevolver":
                    found.setdefault(top.lower(), set()).add(str(path.relative_to(ROOT)))
    return found


def test_every_module_level_import_is_a_declared_dependency():
    declared = _declared()
    missing = {
        module: sorted(files)[:2]
        for module, files in _top_level_imports().items()
        if module not in declared and DISTRIBUTION.get(module, module) not in declared
    }
    assert not missing, (
        "these are imported at module level but declared nowhere in pyproject.toml — a "
        "clean install fails on the first import:\n"
        + "\n".join(f"  {m}: {f}" for m, f in sorted(missing.items()))
    )


def test_the_alias_table_only_names_modules_that_are_imported():
    """A stale alias silently excuses whatever module name it happens to match."""
    imported = set(_top_level_imports())
    # An alias may legitimately cover an import inside an excluded directory, so this
    # only fails for a name nothing anywhere imports.
    everywhere = set()
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        everywhere |= {m.lower() for m in re.findall(r"^\s*(?:from|import)\s+(\w+)", text, re.M)}
    stale = sorted(set(DISTRIBUTION) - everywhere - imported)
    assert not stale, f"these aliases match nothing that is imported: {stale}"
