#!/usr/bin/env python3
"""Rewrite each plugin's `PLUGIN.md` header from the plugin the registry actually holds.

`agentevolver/plugins/README.md` says a manifest's facts are generated from the code so
they cannot drift from it. They were not: the 88 manifests were written by hand, and a
tool count or an `implemented:` tally is exactly the kind of number that is right the day
it is typed and wrong the week after.

Only the derivable half is rewritten — the YAML frontmatter and the `## Tools` table.
Everything else in the file is prose somebody wrote about the service, and prose a
generator overwrites is prose nobody writes again.

`credentials:` and `requirements:` looked like the two fields the code could not state,
and they are not: a credential is named by a `_secret` literal or a tool's `key_env`, and
a dependency by the lazy `import` inside `__call__`. Both are also named in the *family
template* a tool inherits from, which is why a scan of the package alone concluded that
half the registry needed no credentials — and why the hand-written manifests were never
wrong, only short. Reading them from the code found 33 manifests missing a credential
their own plugin reads, several of them the plugin's primary key.

    scripts/gen_plugin_manifest.py            # rewrite every PLUGIN.md
    scripts/gen_plugin_manifest.py --check    # fail if any is out of date
    scripts/gen_plugin_manifest.py --sources  # what the code says each plugin reads
"""

from __future__ import annotations

import argparse
import ast
import difflib
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PLUGIN_ROOT = ROOT / "agentevolver" / "plugins" / "default"
MANIFEST = "PLUGIN.md"
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TOOLS_SECTION = re.compile(r"(?ms)^## Tools\s*\n.*?(?=^## |\Z)")


def _plugins() -> List[Any]:
    """Every registered plugin class, instantiated. Import order runs the decorators."""
    import agentevolver.plugins.default  # noqa: F401 — the import is the registration
    from agentevolver.registry import PLUGIN

    return [cls() for cls in PLUGIN._module_dict.values()]


def _package_dir(plugin: Any) -> Optional[Path]:
    """The directory a plugin's manifest lives in, or None for a class with no file."""
    import inspect

    try:
        return Path(inspect.getfile(type(plugin))).parent
    except Exception:  # noqa: BLE001 — a runtime-defined plugin has no package
        return None


def _existing(path: Path) -> Tuple[Dict[str, str], str]:
    """One manifest's frontmatter lines (by key) and its body."""
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields, text[match.end():]


def _yaml_list(items: List[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _literal_env_names(source: str) -> List[str]:
    """Upper-case string literals handed to a ``_secret`` / ``secret`` call."""
    found: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if target not in {"_secret", "secret"}:
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str) \
                    and argument.value.isupper():
                found.append(argument.value)
    return found


def _secret_env_names(plugin: Any) -> List[str]:
    """Environment variables this plugin's code actually reads a credential from.

    Read from the code rather than from the manifest beside it, because comparing
    the two is the only way a declared `credentials:` can be checked instead of
    believed. Three places name one, and a check that reads only the first
    concludes that half the registry needs no credentials at all:

    * a literal in a ``_secret`` call inside the plugin package;
    * a tool's ``key_env`` field, which is what the call is usually handed;
    * a literal in the **family template** the tool inherits from —
      ``VectorStorePluginTool`` asks for ``OPENAI_API_KEY`` on behalf of every
      vector store, and that call lives in ``plugins/types.py``, not in the package.
    """
    import inspect as _inspect

    names: List[str] = []
    try:
        package = Path(_inspect.getfile(type(plugin))).parent
    except Exception:  # noqa: BLE001 — a runtime-defined plugin has no package
        package = None
    if package is not None:
        for file in sorted(package.rglob("*.py")):
            names += _literal_env_names(file.read_text(encoding="utf-8", errors="ignore"))

    for tool in plugin.tool_list():
        field = getattr(type(tool), "model_fields", {}).get("key_env")
        default = getattr(field, "default", None) if field is not None else None
        if isinstance(default, str) and default:
            names.append(default)
        for base in type(tool).__mro__[1:]:
            if getattr(base, "__module__", "") != "agentevolver.plugins.types":
                continue
            try:
                names += _literal_env_names(_inspect.getsource(base))
            except Exception:  # noqa: BLE001 — a base with no readable source says nothing
                pass
    return sorted(set(names))


def _lazy_imports(source: str) -> List[str]:
    """Modules imported *inside a function* — the lazy provider SDK imports."""
    out: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for scope in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for node in ast.walk(scope):
            if isinstance(node, ast.Import):
                out += [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                out.append(node.module.split(".")[0])
    return out


def _requirement_names(plugin: Any) -> List[str]:
    """pip packages this plugin's tools import, read from the imports themselves.

    Provider SDKs are imported lazily inside ``__call__`` so a plugin registers
    without them, which makes the import statement the one place that states the
    dependency. Scanned in the same two places a credential is — the package, and
    the family template a tool inherits from, which is where a vector store's
    ``langchain_openai`` actually comes from.
    """
    import inspect as _inspect

    names: List[str] = []
    try:
        package = Path(_inspect.getfile(type(plugin))).parent
    except Exception:  # noqa: BLE001 — a runtime-defined plugin has no package
        package = None
    if package is not None:
        for file in sorted(package.rglob("*.py")):
            names += _lazy_imports(file.read_text(encoding="utf-8", errors="ignore"))
    for tool in plugin.tool_list():
        for base in type(tool).__mro__[1:]:
            if getattr(base, "__module__", "") != "agentevolver.plugins.types":
                continue
            try:
                names += _lazy_imports(_inspect.getsource(base))
            except Exception:  # noqa: BLE001
                pass
    ignored = set(sys.stdlib_module_names) | {"agentevolver", "pydantic", "typing"}
    return sorted({n for n in names if n not in ignored})


def _tools_table(plugin: Any) -> str:
    """The `## Tools` section: one row per tool, status derived from the class."""
    tools = sorted(plugin.tool_list(), key=lambda tool: tool.name)
    rows = [f"| `{tool.id}` | {tool.display_name or tool.name} | "
            f"{'✅' if tool.implemented else '🚧'} | {tool.description} |"
            for tool in tools]
    done = sum(1 for tool in tools if tool.implemented)
    if not tools:
        tally = "This plugin provides no tools."
    elif done == len(tools):
        tally = f"All {len(tools)} tools are implemented."
    else:
        tally = f"{done} of {len(tools)} tools are implemented; the rest are registered stubs."
    declared = [f"- `{tool.id}` → `data` keys: {', '.join(sorted(type(tool).output))}"
                for tool in tools if type(tool).output]
    body = ["## Tools", "",
            "| id | name | status | what it does |",
            "|----|------|--------|--------------|",
            *rows, "", tally, ""]
    if declared:
        body += ["Declared result shapes:", "", *declared, ""]
    return "\n".join(body) + "\n"


def render(plugin: Any, package: Path) -> str:
    """The manifest this plugin should have: derived header, existing prose."""
    path = package / MANIFEST
    fields, body = _existing(path)
    tools = plugin.tool_list()
    icon = fields.get("icon") or ("resources/icon.svg" if (package / "resources" / "icon.svg").exists() else "")
    header = {
        "id": plugin.name,
        "name": plugin.display_name or plugin.name,
        "category": plugin.category,
        "type": plugin.type,
        "icon": icon,
        "tools": str(len(tools)),
        "implemented": str(sum(1 for tool in tools if tool.implemented)),
        "credentials": _yaml_list(_secret_env_names(plugin)),
        "requirements": _yaml_list(_requirement_names(plugin)),
        "version": fields.get("version", '"1.0.0"'),
    }
    # An empty value is not a fact about the plugin, so the key is left out rather
    # than written blank — a manifest saying `icon:` reads as a broken icon.
    front = ("---\n" + "".join(f"{key}: {value}\n" for key, value in header.items() if value != "")
             + "---\n")

    table = _tools_table(plugin)
    if TOOLS_SECTION.search(body):
        body = TOOLS_SECTION.sub(lambda _: table, body, count=1)
    else:
        title = f"# {plugin.display_name or plugin.name}\n\n{plugin.description}\n\n"
        body = (body or title) + "\n" + table
    return front + body.lstrip("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if any manifest is out of date")
    parser.add_argument("--sources", action="store_true",
                        help="print what the code says each plugin reads and imports")
    args = parser.parse_args()

    plugins = _plugins()
    drift, written = [], 0
    for plugin in plugins:
        package = _package_dir(plugin)
        if package is None:
            continue
        path = package / MANIFEST
        expected = render(plugin, package)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if args.sources:
            print(f"  {plugin.name:<18} credentials={_secret_env_names(plugin) or '—'}  "
                  f"requirements={_requirement_names(plugin) or '—'}")
            continue
        if args.check:
            if current != expected:
                drift.append("".join(difflib.unified_diff(
                    current.splitlines(keepends=True), expected.splitlines(keepends=True),
                    fromfile=str(path.relative_to(ROOT)), tofile="generated")))
            continue
        if current != expected:
            path.write_text(expected, encoding="utf-8")
            written += 1

    if args.sources:
        return 0

    if args.check:
        if drift:
            print("".join(drift), end="", file=sys.stderr)
            print(f"\n{len(drift)} PLUGIN.md file(s) are out of date; run "
                  f"scripts/gen_plugin_manifest.py", file=sys.stderr)
            return 1
        print(f"{len(plugins)} PLUGIN.md files match the registry")
        return 0

    print(f"wrote {written} of {len(plugins)} PLUGIN.md files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
